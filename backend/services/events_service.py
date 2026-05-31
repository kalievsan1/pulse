"""Events service — live parsing from kino.kz, Ticketon, and 2GIS."""

import logging
import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

logger = logging.getLogger(__name__)

KINOKZ_BASE = 'https://kino.kz/ru'
TICKETON_BASE = 'https://ticketon.kz'
DGIS_BASE = 'https://catalog.api.2gis.com/3.0/items'
KINO_CATEGORIES = {
    'concert': 'Концерты',
    'theatre': 'Театры',
    'standup': 'Стендап',
    'sport': 'Спорт',
    'art': 'Искусство',
    'entertainment': 'Развлечения',
    'family': 'Семейные',
    'tours': 'Туры',
}
CATEGORIES = {
    **KINO_CATEGORIES,
    'bowling': 'Боулинг',
    'billiards': 'Бильярд',
    'karaoke': 'Караоке',
    'quests': 'Квесты',
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
DGIS_CATEGORIES = {
    'bowling': ('боулинг', 'Боулинг'),
    'billiards': ('бильярд', 'Бильярд'),
    'karaoke': ('караоке', 'Караоке'),
    'quests': ('квесты', 'Квесты'),
    'entertainment': ('развлечения', 'Развлечения'),
    'family': ('детские развлечения', 'Семейные'),
    'sport': ('спортивные развлечения', 'Спорт'),
}
DGIS_DEFAULT_CATEGORIES = ('bowling', 'billiards', 'karaoke', 'quests')
CITIES = {
    '1': 'Астана',
    '2': 'Алматы',
}
DGIS_CITIES = {
    'almaty': {
        'aliases': ('алматы', 'almaty'),
        'name': 'Алматы',
        'slug': 'almaty',
        'location': '76.92861,43.25667',
    },
    'astana': {
        'aliases': ('астана', 'astana', 'нур-султан', 'nur-sultan', 'nursultan'),
        'name': 'Астана',
        'slug': 'astana',
        'location': '71.44907,51.16939',
    },
}
TIMEOUT = 15
DGIS_TIMEOUT = 6
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

    def _2gis_api_key(self):
        """Read the 2GIS API key lazily so env changes are picked up after restart."""
        return (
            os.environ.get('DGIS_API_KEY')
            or os.environ.get('TWOGIS_API_KEY')
            or os.environ.get('GIS2_API_KEY')
            or os.environ.get('2GIS_API_KEY')
        )

    def _2gis_city(self, city=None):
        if city:
            city_lower = city.lower()
            for meta in DGIS_CITIES.values():
                if any(alias in city_lower for alias in meta['aliases']):
                    return meta
        return DGIS_CITIES['almaty']

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
            'type': event_type or (KINO_CATEGORIES.get(category, '') if category else ''),
            'venue': venue,
            'city': city,
            'date': next_date,
            'time': '',
            'price': price,
            'image': poster,
            'age': age_label,
            'description': raw.get('presentation', '') or '',
            'genres': [],
            'category': category or '',
            'source': 'kino.kz',
            'url': f'https://kino.kz/ru/{category}/event/{event_id}' if category else '',
        }

    def _fetch_kino_category(self, category, city_id):
        cache_key = f'browse:{category}:{city_id}'
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        url = f'{KINOKZ_BASE}/{category}'
        try:
            r = self.session.get(
                url,
                cookies={'city': city_id},
                timeout=TIMEOUT,
            )
            if r.status_code != 200:
                logger.warning('kino.kz %s returned %s', category, r.status_code)
                return []

            raw_events = self._extract_events_from_rsc(r.text)
            formatted = [self._format_event(e, category=category) for e in raw_events]
            seen = set()
            unique = []
            for e in formatted:
                if e['id'] in seen:
                    continue
                seen.add(e['id'])
                unique.append(e)
            self._set_cached(cache_key, unique)
            return unique
        except Exception:
            logger.exception('Error fetching kino.kz category %s', category)
            return []

    def _browse_kino(self, categories, city_id):
        if not categories:
            return []
        if len(categories) == 1:
            return self._fetch_kino_category(categories[0], city_id)

        events = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_map = {
                executor.submit(self._fetch_kino_category, cat, city_id): cat
                for cat in categories
            }
            for future in as_completed(future_map):
                try:
                    events.extend(future.result())
                except Exception:
                    logger.exception('Kino.kz category future failed: %s', future_map[future])
        return events

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

        event_type = KINO_CATEGORIES.get(category, '')
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
            'category': category or '',
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
        if category and category not in TICKETON_CATEGORIES:
            return []

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

    def _format_2gis_schedule(self, schedule):
        if not isinstance(schedule, dict):
            return ''
        if schedule.get('is_24x7'):
            return '24/7'
        if schedule.get('description'):
            return self._normalize_text(schedule.get('description'))
        if schedule.get('comment'):
            return self._normalize_text(schedule.get('comment'))
        return ''

    def _format_2gis_rating(self, reviews):
        if not isinstance(reviews, dict):
            return ''
        rating = reviews.get('rating') or reviews.get('general_rating') or reviews.get('org_rating')
        if rating in (None, ''):
            return ''
        try:
            return f'{float(rating):.1f}'
        except (TypeError, ValueError):
            return str(rating)

    def _format_2gis_place(self, raw, category=None, city=None, query=None):
        place_id = raw.get('id')
        title = self._normalize_text(raw.get('name') or raw.get('full_name') or '')
        if not place_id or not title:
            return None

        city_meta = self._2gis_city(city)
        rubrics = [
            self._normalize_text(r.get('name'))
            for r in raw.get('rubrics', [])
            if isinstance(r, dict) and r.get('name')
        ]
        event_type = (DGIS_CATEGORIES.get(category) or (query, query or ''))[1]
        address = self._normalize_text(
            raw.get('full_address_name')
            or raw.get('address_name')
            or (raw.get('address') or {}).get('building_name')
            or ''
        )
        rating = self._format_2gis_rating(raw.get('reviews'))
        review_count = ''
        reviews = raw.get('reviews') or {}
        if isinstance(reviews, dict):
            review_count = reviews.get('review_count') or reviews.get('general_review_count') or ''
        schedule = self._format_2gis_schedule(raw.get('schedule'))
        summary = ''
        if isinstance(raw.get('summary'), dict):
            summary = self._normalize_text(raw['summary'].get('text'))
        if not summary and rubrics:
            summary = ', '.join(rubrics[:3])

        return {
            'id': f'2gis:{place_id}',
            'title': title,
            'type': event_type,
            'venue': address,
            'city': city_meta['name'],
            'date': '',
            'time': schedule,
            'price': '',
            'image': '',
            'age': '',
            'description': summary,
            'genres': rubrics or ([event_type] if event_type else []),
            'category': category or '',
            'source': '2gis.kz',
            'url': f'https://2gis.kz/{city_meta["slug"]}/firm/{quote(str(place_id), safe="")}',
            'rating': rating,
            'review_count': str(review_count) if review_count else '',
        }

    def _fetch_2gis_query(self, query, city=None, category=None, page_size=8):
        api_key = self._2gis_api_key()
        if not api_key:
            return []

        city_meta = self._2gis_city(city)
        cache_key = f'2gis:{query}:{city_meta["slug"]}:{page_size}'
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        params = {
            'key': api_key,
            'q': query,
            'location': city_meta['location'],
            'radius': 40000,
            'page_size': page_size,
            'type': 'branch',
            'locale': 'ru_KZ',
            'fields': ','.join([
                'items.address_name',
                'items.full_address_name',
                'items.rubrics',
                'items.schedule',
                'items.reviews',
                'items.summary',
            ]),
        }

        try:
            r = self.session.get(DGIS_BASE, params=params, timeout=DGIS_TIMEOUT)
            if r.status_code != 200:
                logger.warning('2GIS query "%s" returned HTTP %s', query, r.status_code)
                return []
            payload = r.json()
            if payload.get('meta', {}).get('code') != 200:
                logger.warning('2GIS query "%s" returned API code %s', query, payload.get('meta', {}).get('code'))
                return []
            places = []
            for item in payload.get('result', {}).get('items', []):
                place = self._format_2gis_place(item, category=category, city=city, query=query)
                if place:
                    places.append(place)
            self._set_cached(cache_key, places)
            return places
        except Exception:
            logger.exception('Error fetching 2GIS query "%s"', query)
            return []

    def _browse_2gis(self, city=None, category=None):
        if not self._2gis_api_key():
            return []

        if category and category not in DGIS_CATEGORIES:
            return []

        categories_to_fetch = [category] if category else list(DGIS_DEFAULT_CATEGORIES)
        if len(categories_to_fetch) == 1:
            cat = categories_to_fetch[0]
            query, _ = DGIS_CATEGORIES[cat]
            return self._fetch_2gis_query(query, city=city, category=cat, page_size=10)

        events = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_map = {
                executor.submit(
                    self._fetch_2gis_query,
                    DGIS_CATEGORIES[cat][0],
                    city,
                    cat,
                    5,
                ): cat
                for cat in categories_to_fetch
            }
            for future in as_completed(future_map):
                try:
                    events.extend(future.result())
                except Exception:
                    logger.exception('2GIS category future failed: %s', future_map[future])
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
        categories_to_fetch = [category] if category in KINO_CATEGORIES else list(KINO_CATEGORIES.keys()) if not category else []

        # Determine city cookie
        city_id = '2'  # Default Almaty
        if city:
            city_lower = city.lower()
            if 'астана' in city_lower or 'astana' in city_lower:
                city_id = '1'
            elif 'алматы' in city_lower or 'almaty' in city_lower:
                city_id = '2'

        all_events = []
        source_futures = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            if categories_to_fetch:
                source_futures[executor.submit(self._browse_kino, categories_to_fetch, city_id)] = 'kino.kz'
            source_futures[executor.submit(self._browse_ticketon, city, category)] = 'ticketon.kz'
            source_futures[executor.submit(self._browse_2gis, city, category)] = '2gis.kz'

            for future in as_completed(source_futures):
                try:
                    all_events.extend(future.result())
                except Exception:
                    logger.exception('Events source failed: %s', source_futures[future])

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

    def search(self, query, city=None):
        """Search events across all categories."""
        q = query.lower()
        # Fetch all categories and filter
        all_events = self.browse(city=city, limit=200)
        results = [
            e for e in all_events
            if q in e['title'].lower()
            or q in e.get('venue', '').lower()
            or q in e.get('type', '').lower()
            or q in e.get('city', '').lower()
        ]
        results.extend(self._fetch_2gis_query(query, city=city, page_size=12))

        seen = set()
        deduped = []
        for e in results:
            key = e.get('id') or self._event_dedupe_key(e)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(e)
        return deduped[:80]

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
