"""Stash package.

Stash turns uploaded PDF/PPTX study material into a scrollable feed of
bite-size idea cards. This package owns its own persistence (stash_* tables in
db._SCHEMA), parsing, AI generation pipeline, background job worker, HTML pages
and JSON API. Register it from app.py via get_stash_blueprints().
"""

from . import config, repository, schemas, service

__all__ = ["config", "repository", "schemas", "service", "get_stash_blueprints"]


def get_stash_blueprints():
    """Return the stash page and API blueprints (registered in app.py)."""
    from .routes_pages import stash_pages
    from .routes_api import stash_api
    return [stash_pages, stash_api]


def ensure_worker():
    """Start the in-process background job worker (safe to call repeatedly)."""
    from .worker import ensure_worker as _ensure_worker

    return _ensure_worker()