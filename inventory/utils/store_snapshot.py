"""Utilities for exporting and restoring store data snapshots."""
from pathlib import Path

from django.conf import settings

SNAPSHOT_EXCLUDED_MODELS = (
    'contenttypes',
    'auth.Permission',
    'sessions.session',
    'inventory.OperationLog',
    'admin.LogEntry',
)


def get_store_snapshot_path(path: str | None = None) -> Path:
    """Resolve the JSON snapshot path."""
    if path:
        return Path(path).expanduser().resolve()
    return Path(settings.STORE_SNAPSHOT_PATH).expanduser().resolve()


def get_local_view_db_path() -> Path:
    """Resolve the dedicated local-view SQLite path."""
    return Path(settings.LOCAL_VIEW_DB_PATH).expanduser().resolve()
