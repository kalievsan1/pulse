web: python backend/init_db.py && gunicorn --chdir backend "app:create_app()" --bind 0.0.0.0:${PORT:-5000}
