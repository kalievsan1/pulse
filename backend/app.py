import os
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, request, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from config import Config

def setup_logging(app):
    log_dir = os.path.join(os.path.dirname(__file__), 'logs')
    os.makedirs(log_dir, exist_ok=True)

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'app.log'),
        maxBytes=1_000_000,
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    ))
    file_handler.setLevel(logging.INFO)

    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('Pulse application started')


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60 per minute"],
    storage_uri="memory://",
)


def create_app():
    Config.validate()

    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    app = Flask(__name__, static_folder=static_dir, static_url_path='')
    app.config['SECRET_KEY'] = Config.SECRET_KEY
    app.config['JWT_SECRET_KEY'] = Config.JWT_SECRET_KEY
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = Config.JWT_ACCESS_TOKEN_EXPIRES

    CORS(app, origins=Config.CORS_ORIGINS, supports_credentials=True)
    JWTManager(app)
    limiter.init_app(app)
    setup_logging(app)

    from database import apply_admin_password_reset_from_env, init_db
    init_db()
    apply_admin_password_reset_from_env()

    from routes.auth import auth_bp
    from routes.movies import movies_bp
    from routes.books import books_bp
    from routes.music import music_bp
    from routes.events import events_bp
    from routes.profile import profile_bp
    from routes.recommendations import recommendations_bp
    from routes.admin import admin_bp
    from routes.ai_chat import ai_chat_bp
    from routes.assistant import assistant_bp
    from routes.hdrezka import hdrezka_bp
    from routes.cors_proxy import cors_proxy_bp

    # Apply specific rate limits
    limiter.limit("3 per minute")(auth_bp)
    limiter.limit("10 per minute")(ai_chat_bp)
    limiter.limit("30 per minute")(profile_bp)
    limiter.limit("20 per minute")(admin_bp)

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(movies_bp, url_prefix='/api/movies')
    app.register_blueprint(books_bp, url_prefix='/api/books')
    app.register_blueprint(music_bp, url_prefix='/api/music')
    app.register_blueprint(events_bp, url_prefix='/api/events')
    app.register_blueprint(profile_bp, url_prefix='/api/profile')
    app.register_blueprint(recommendations_bp, url_prefix='/api/recommendations')
    app.register_blueprint(admin_bp, url_prefix='/api/admin')
    app.register_blueprint(ai_chat_bp, url_prefix='/api/ai')
    app.register_blueprint(assistant_bp, url_prefix='/api/assistant')
    app.register_blueprint(hdrezka_bp, url_prefix='/api/hdrezka')
    app.register_blueprint(cors_proxy_bp)  # No prefix - uses /api/proxy/...

    @app.route('/api/health')
    def health():
        return {'status': 'ok', 'gemini_configured': bool(Config.GEMINI_API_KEY)}

    @app.route('/manifest.json')
    def manifest():
        resp = send_from_directory(static_dir, 'manifest.json')
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp

    @app.route('/service-worker.js')
    def service_worker():
        resp = send_from_directory(static_dir, 'service-worker.js', mimetype='application/javascript')
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp

    @app.route('/')
    def index():
        return send_from_directory(static_dir, 'index.html')

    @app.errorhandler(404)
    def spa_fallback(error):
        path = request.path.lstrip('/')
        filename = os.path.basename(path)

        if request.path.startswith('/api/'):
            return {'error': 'Not found'}, 404

        if '.' in filename:
            return {'error': 'Not found'}, 404

        return send_from_directory(static_dir, 'index.html')

    return app


if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get('PORT', '5000'))
    app.run(debug=False, host='0.0.0.0', port=port)
