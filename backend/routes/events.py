"""Events routes — browse, search, details from kino.kz."""

import logging
from flask import Blueprint, request, jsonify
from services.events_service import events_service

logger = logging.getLogger(__name__)
events_bp = Blueprint('events', __name__)


@events_bp.route('/browse')
def browse():
    """Browse events with optional city, type, and category filters."""
    try:
        city = request.args.get('city')
        event_type = request.args.get('type')
        category = request.args.get('category')
        return jsonify(events_service.browse(city=city, event_type=event_type, category=category))
    except Exception as e:
        logger.exception('Error browsing events')
        return jsonify({'error': 'Service temporarily unavailable'}), 503


@events_bp.route('/search')
def search():
    """Search events by query string."""
    q = request.args.get('q', '')
    city = request.args.get('city')
    if not q:
        return jsonify([])
    try:
        return jsonify(events_service.search(q, city=city))
    except Exception as e:
        logger.exception('Error searching events: q=%s', q)
        return jsonify({'error': 'Service temporarily unavailable'}), 503


@events_bp.route('/detail/<event_id>')
def event_detail(event_id):
    """Get detailed information about a specific event."""
    try:
        event = events_service.get_event(event_id)
        if not event:
            return jsonify({'error': 'Not found'}), 404
        return jsonify(event)
    except Exception as e:
        logger.exception('Error fetching event %s', event_id)
        return jsonify({'error': 'Service temporarily unavailable'}), 503


@events_bp.route('/types')
def types():
    """Get list of available event types."""
    try:
        return jsonify(events_service.get_types())
    except Exception as e:
        logger.exception('Error fetching event types')
        return jsonify({'error': 'Service temporarily unavailable'}), 503


@events_bp.route('/cities')
def cities():
    """Get list of cities with events."""
    try:
        return jsonify(events_service.get_cities())
    except Exception as e:
        logger.exception('Error fetching cities')
        return jsonify({'error': 'Service temporarily unavailable'}), 503


@events_bp.route('/categories')
def categories():
    """Get list of event categories for tabs."""
    try:
        return jsonify(events_service.get_categories())
    except Exception as e:
        logger.exception('Error fetching categories')
        return jsonify({'error': 'Service temporarily unavailable'}), 503
