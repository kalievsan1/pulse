# Pulse

Pulse is a Flask web application with a static React/PWA frontend for personalized entertainment recommendations. It combines movies, books, music, events, favorites, ratings, watchlists, activity history, and an AI assistant in one interface.

## Features

- User registration and JWT authentication
- Movie, book, music, and event catalogs
- Favorites, ratings, watchlist, reminders, and activity log
- Personalized recommendations based on user behavior
- AI chat assistant powered by Google Gemini when `GOOGLE_API_KEY` is configured
- Admin endpoints for content rules, pinned items, audit, and settings
- Mobile-friendly static frontend served from `backend/static`

## Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp backend/.env.example backend/.env
python backend/init_db.py
python backend/app.py
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item backend\.env.example backend\.env
python backend\init_db.py
python backend\app.py
```

The app runs on `http://127.0.0.1:5000`.

## Environment Variables

Required:

- `SECRET_KEY`
- `JWT_SECRET_KEY`

Optional:

- `KINOPOISK_API_KEY`
- `GOOGLE_BOOKS_API_KEY`
- `GOOGLE_API_KEY`
- `DGIS_API_KEY` for 2GIS Places API results in the Events tab
- `GEMINI_PRIMARY_MODEL`
- `GEMINI_FALLBACK_MODEL`
- `DEFAULT_DAILY_LIMIT`
- `DATABASE_PATH`
- `CORS_ORIGINS`

## Railway

The repository includes:

- `railway.json`
- `Procfile`
- `build.sh`

Railway should install dependencies with `pip install -r requirements.txt`, initialize the database, and start the app with Gunicorn.

For persistent SQLite storage, create a Railway volume and set:

```bash
DATABASE_PATH=/data/entertainment.db
```

Then mount the volume at `/data`.

## Tests

```bash
cd backend
python -m pytest tests -q --tb=short --ignore=tests/test_hdrezka_api.py --ignore=tests/test_hdrezka_integration.py
```
