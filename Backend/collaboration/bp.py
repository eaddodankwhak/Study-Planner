"""Blueprint shared across all collaboration route modules."""

from flask import Blueprint

collab_bp = Blueprint("collab", __name__, url_prefix="/workspaces")