"""Events service — live parsing from kino.kz and Ticketon for Kazakhstan events."""

import logging
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

KINOKZ_BASE = 'https://kino.kz/ru'
TICKETON_BASE = 'https://ticketon.kz'
CATEGORIES = {
    'concert': 'Концерты',
    'theatre': 'Театры',
    'standup': 'Стендап',
    'sport': 'Спорт',
    'art': 'Искусство',
    'entertainment': 'Развлечения',
    'family': 'Семейные',
    'tours': 'Туры',
}
TICKETON_CATEGORIES = {
    'concert': 'concerts',
    'theatre': 'theatres',
    'standup': 'stand-up',
    'sport': 'sports',
    'art': 'museums',
    'entertainment': 'entertainment',
    'family': 'children',
    'tours': 'tours',
}
CITIES = {
    '1': 'Астана',
    '2': 'Алматы',
}
TIMEOUT = 15
CACHE_TTL = 600  # 10 minutes


class KinoKzParser:
    """Parses event data from kino.kz RSC payloads."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'ru,en;q=0.9',
        })
        self._cache = {}  # key -> (data, timestamp)

    def _get_cached(self, key):
        if key in self._cache:
            data, ts = self._cache[key]
            if time.time() - ts < CACHE_TTL:
                return data
        return None

    def _set_cached(self, key, data):
        self._cache[key] = (data, time.time())

    def _normalize_text(self, value):
        return re.sub(r'\s+', ' ', (value or '').replace('\xa0', ' ')).strip()

    def _ticketon_city(self, city):
        """Return Ticketon city slug/display pair for supported city filters."""
        if not city:
            return None, ''

        city_lower = city.lower()
        if 'astana' in city_lower or 'астана' in city_lower:
            return 'astana', 'Астана'
        if 'almaty' in city_lower or 'алматы' in city_lower:
            return 'almaty', 'Алматы'
        return None, ''

    def _infer_city(self, text):
        haystack = (text or '').lower()
        known_cities = (
            'Алматы', 'Астана', 'Шымкент', 'Караганда', 'Атырау', 'Актау',
            'Актобе', 'Павлодар', 'Уральск', 'Костанай', 'Кызылорда',
            'Тараз', 'Семей', 'Усть-Каменогорск', 'Туркестан',
        )
        for city_name in known_cities:
            if city_name.lower() in haystack:
                return city_name
        return ''

    def _extract_events_from_rsc(self, html):
        """Extract event objects from Next.js RSC payload in HTML."""
        seen_ids = set()
        events = []
        # Find all self.__next_f.push blocks
        for match in re.finditer(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL):
            raw = match.group(1)
            try:
                unescaped = raw.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
            except Exception:
                continue

            # Find event JSON objects by balanced brace matching
            for m in re.finditer(r'\{"id":\d+,', unescaped):
                text = unescaped[m.start():]
                depth = 0
                end = 0
                for i, c in enumerate(text):
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if end == 0:
                    continue
                try:
                    obj = json.loads(text[:end])
                    # Must have next_session_date to be an event listing
                    if 'next_session_date' not in obj:
                        continue
                    eid = obj.get('id')
                    if eid in seen_ids:
                        continue
                    seen_ids.add(eid)
                    events.append(obj)
                except (json.JSONDecodeError, ValueError):
                    pass
        return events

    def _format_event(self, raw, category=None):
        """Convert raw kino.kz event object to our API format."""
        event_id = raw.get('id', '')
        name = raw.get('name') or raw.get('name_rus') or raw.get('name_origin', '')
        venue = raw.get('partner_name', '')
        city = raw.get('partner_city_name', '')
        poster = raw.get('small_poster', '')
        if not poster:
            posters = raw.get('posters', {})
            poster = posters.get('p344x489') or posters.get('p168x242', '')
        price_from = raw.get('price_from')
        price = f'от {price_from} ₸' if price_from else ''
        next_date = raw.get('next_session_date') or raw.get('premiere_kaz', '')
        event_type = raw.get('event_type_name', '')
        age = raw.get('age_restriction')
        age_label = f'{age}+' if age else ''

        return {
            'id': event_id,
            'title': name,
            'type': event_type or (CATEGORIES.get(category, '') if category else ''),
            'venue': venue,
            'city': city,
            'date': next_date,
            'time': '',
            'price': price,
            'image': poster,
            'age': age_label,
            'description': raw.get('presentation', '') or '',
            'genres': [],
            'source': 'kino.kz',
            'url': f'https://kino.kz/ru/{category}/event/{event_id}' if category else '',
        }

    def _ticketon_url(self, category=None, city=None):
        city_slug, _ = self._ticketon_city(city)
        path = TICKETON_CATEGORIES.get(category, '')

        pieces = [TICKETON_BASE]
        if city_slug:
            pieces.append(city_slug)
        if path:
            pieces.append(path)

        return '/'.join(piece.strip('/') for piece in pieces)

    def _extract_ticketon_venue(self, card_text, time_text):
        if not card_text or not time_text or time_text not in card_text:
            return ''

        after_time = card_text.split(time_text, 1)[1]
        before_price = re.split(r'\b(?:От|от)\s+', after_time, 1)[0]
        return self._normalize_text(before_price)

    def _format_tenge(self, amount):
        try:
            normalized = f'{int(amount):,}'.replace(',', ' ')
        except (TypeError, ValueError):
            return ''
        return f'От {normalized} \u20b8'

    def _extract_ticketon_meta(self, html):
        """Extract extra event details from Ticketon's SSR payload."""
        meta = {}
        for match in re.finditer(r'slug:"([^"]+)"', html):
            slug = match.group(1)
            chunk = html[max(0, match.start() - 5000):min(len(html), match.end() + 2000)]

            venue = ''
            venue_match = re.search(r'venue_name:"([^"]+)"', chunk)
            if not venue_match:
                venue_match = re.search(r'venue:\$R\[\d+\]=\{[^{}]*?name:"([^"]+)"', chunk)
            if venue_match:
                venue = self._normalize_text(venue_match.group(1))

            city_name = ''
            city_match = re.search(r'cities:\$R\[\d+\]=\[\$R\[\d+\]=\{[^{}]*?name:"([^"]+)"', chunk)
            if city_match:
                city_name = self._normalize_text(city_match.group(1))

            date = ''
            date_match = re.search(r'(?:first_session_time|next_session_time):"([^"]+)"', chunk)
            if date_match:
                date = date_match.group(1)

            price = ''
            price_match = re.search(r'min_price:(\d+)', chunk)
            if price_match:
                price = self._format_tenge(price_match.group(1))

            age = ''
            age_match = re.search(r'age_categories:.*?name:"([^"]+\+)"', chunk)
            if age_match:
                age = self._normalize_text(age_match.group(1))

            meta[slug] = {
                'venue': venue,
                'city': city_name,
                'date': date,
                'price': price,
                'age': age,
            }
        return meta

    def _format_ticketon_card(self, anchor, category=None, city=None, meta=None):
        title_el = anchor.find(['h2', 'h3'])
        time_el = anchor.find('time')
        if not title_el or not time_el:
            return None

        href = anchor.get('href', '')
        slug = href.rstrip('/').split('/')[-1]
        title = self._normalize_text(title_el.get_text(' ', strip=True))
        if not slug or not title:
            return None

        card_text = self._normalize_text(anchor.get_text(' ', strip=True))
        time_text = self._normalize_text(time_el.get_text(' ', strip=True))
        meta = meta or {}
        date = (time_el.get('datetime') or meta.get('date') or '').strip()
        if not date:
            return None

        image_el = anchor.find('img', src=True)
        image = image_el.get('src', '') if image_el else ''
        if image.startswith('/'):
            image = f'{TICKETON_BASE}{image}'

        price_matches = re.findall(r'(?:От|от)\s+[\d\s\xa0]+\u20b8', card_text)
        price = self._normalize_text(price_matches[-1]) if price_matches else meta.get('price', '')

        age_el = anchor.find(attrs={'role': 'status'})
        age = self._normalize_text(age_el.get_text(' ', strip=True)) if age_el else meta.get('age', '')
        venue = self._extract_ticketon_venue(card_text, time_text) or meta.get('venue', '')
        _, city_display = self._ticketon_city(city)
        city_name = city_display or meta.get('city', '') or self._infer_city(f'{title} {venue} {card_text}')

        event_type = CATEGORIES.get(category, '')
        return {
            'id': f'ticketon:{slug}',
            'title': title,
            'type': event_type,
            'venue': venue,
            'city': city_name,
            'date': date,
            'time': time_text,
            'price': price,
            'image': image,
            'age': age,
            'description': '',
            'genres': [event_type] if event_type else [],
            'source': 'ticketon.kz',
            'url': f'{TICKETON_BASE}{href}' if href.startswith('/') else href,
        }

    def _extract_ticketon_events(self, html, category=None, city=None):
        soup = BeautifulSoup(html, 'html.parser')
        meta_by_slug = self._extract_ticketon_meta(html)
        events = []
        seen = set()

        for anchor in soup.find_all('a', href=True):
            if '/event/' not in anchor.get('href', ''):
                continue
            slug = anchor.get('href', '').rstrip('/').split('/')[-1]
            event = self._format_ticketon_card(
                anchor,
                category=category,
                city=city,
                meta=meta_by_slug.get(slug, {}),
            )
            if not event:
                continue
            if event['id'] in seen:
                continue
            seen.add(event['id'])
            events.append(event)

        return events

    def _fetch_ticketon_category(self, category=None, city=None):
        cache_key = f'ticketon:{category or "all"}:{city or "all"}'
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        url = self._ticketon_url(category=category, city=city)
        try:
            r = self.session.get(url, timeout=TIMEOUT)
            if r.status_code != 200:
                logger.warning('ticketon.kz %s returned %s', url, r.status_code)
                return []

            events = self._extract_ticketon_events(r.text, category=category, city=city)
            self._set_cached(cache_key, events)
            return events
        except Exception:
            logger.exception('Error fetching ticketon.kz url %s', url)
            return []

    def _browse_ticketon(self, city=None, category=None):
        categories_to_fetch = [category] if category else list(TICKETON_CATEGORIES.keys())

        if len(categories_to_fetch) == 1:
            return self._fetch_ticketon_category(categories_to_fetch[0], city=city)

        events = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_map = {
                executor.submit(self._fetch_ticketon_category, cat, city): cat
                for cat in categories_to_fetch
            }
            for future in as_completed(future_map):
                try:
                    events.extend(future.result())
                except Exception:
                    logger.exception('Ticketon category future failed: %s', future_map[future])
        return events

    def _event_dedupe_key(self, event):
        title = self._normalize_text(event.get('title', '')).lower()
        title = re.sub(r'[^\w\s]+', '', title)
        title = re.sub(r'\s+', ' ', title).strip()
        date = (event.get('date') or '')[:10]
        return title, date

    def browse(self, city=None, event_type=None, category=None, limit=50):
        """Browse events, optionally filtered by city/type/category."""
        # If a specific category is requested, fetch only that
        categories_to_fetch = [category] if category else list(CATEGORIES.keys())

        # Determine city cookie
        city_id = '2'  # Default Almaty
        if city:
            city_lower = city.lower()
            if 'астана' in city_lower or 'astana' in city_lower:
                city_id = '1'
            elif 'алматы' in city_lower or 'almaty' in city_lower:
                city_id = '2'

        all_events = []
        for cat in categories_to_fetch:
            cache_key = f'browse:{cat}:{city_id}'
            cached = self._get_cached(cache_key)
            if cached is not None:
                all_events.extend(cached)
                continue

            url = f'{KINOKZ_BASE}/{cat}'
            try:
                r = self.session.get(
                    url,
                    cookies={'city': city_id},
                    timeout=TIMEOUT,
                )
                if r.status_code != 200:
                    logger.warning('kino.kz %s returned %s', cat, r.status_code)
                    continue

                raw_events = self._extract_events_from_rsc(r.text)
                formatted = [self._format_event(e, category=cat) for e in raw_events]
                # Deduplicate by id
                seen = set()
                unique = []
                for e in formatted:
                    if e['id'] not in seen:
                        seen.add(e['id'])
                        unique.append(e)
                self._set_cached(cache_key, unique)
                all_events.extend(unique)
            except Exception:
                logger.exception('Error fetching kino.kz category %s', cat)

        all_events.extend(self._browse_ticketon(city=city, category=category))

        # Global dedup across categories/sources (same event can appear in multiple places)
        seen_global = set()
        deduped = []
        for e in all_events:
            dedupe_key = self._event_dedupe_key(e) or e['id']
            if dedupe_key not in seen_global:
                seen_global.add(dedupe_key)
                deduped.append(e)
        all_events = deduped

        # Apply filters
        if event_type:
            et = event_type.lower()
            all_events = [e for e in all_events if et in e.get('type', '').lower()]

        # Sort by date (soonest first)
        all_events.sort(key=lambda e: e.get('date') or '9999')

        return all_events[:limit]

    def search(self, query, city_id='2'):
        """Search events across all categories."""
        q = query.lower()
        # Fetch all categories and filter
        all_events = self.browse(limit=200)
        return [
            e for e in all_events
            if q in e['title'].lower()
            or q in e.get('venue', '').lower()
            or q in e.get('type', '').lower()
            or q in e.get('city', '').lower()
        ]

    def get_event(self, event_id, category=None):
        """Get event detail. Try cache first, then detail page."""
        # Search in cached browse data
        for key, (data, ts) in self._cache.items():
            if key.startswith('browse:') and time.time() - ts < CACHE_TTL:
                for e in data:
                    if str(e['id']) == str(event_id):
                        return e
        return None

    def get_types(self):
        """Return available event types."""
        return list(CATEGORIES.values())

    def get_cities(self):
        """Return available cities."""
        return list(CITIES.values())

    def get_categories(self):
        """Return category slugs and names for frontend tabs."""
        return [{'id': k, 'name': v} for k, v in CATEGORIES.items()]


events_service = KinoKzParser()
