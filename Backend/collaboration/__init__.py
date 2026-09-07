"""Collaborative Workspace package.

Exposes the HTML blueprint for workspaces, work items, comments, information
requests, and daily logs. Register via register_collaboration_blueprint(app).
"""

from . import bp, models, services

__all__ = ["bp", "models", "services", "get_collaboration_blueprint"]


def get_collaboration_blueprint():
    """Return the collaboration HTML blueprint (registers all route modules)."""
    from . import (routes_comments, routes_logs, routes_requests,
                   routes_work_items, routes_workspaces)
    return bp.collab_bp