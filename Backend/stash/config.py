"""Configuration and hard limits for the Stash feature.

Every tunable lives here (read from environment once at import) so the rest of
the stash package refers to a single source of truth instead of scattering
magic numbers across modules. All values can be overridden through environment
variables, mirroring the build prompt's "safe defaults, env-configurable"
requirement.
"""

import os

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))


def _env_int(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class StashConfig:
    """Runtime configuration for Stash (environment-overridable defaults)."""

    def __init__(self):
        # Upload limits.
        self.max_file_bytes = _env_int("STASH_MAX_FILE_BYTES", 50 * 1024 * 1024)
        self.max_pdf_pages = _env_int("STASH_MAX_PDF_PAGES", 600)
        self.max_pptx_slides = _env_int("STASH_MAX_PPTX_SLIDES", 200)

        # Generation preferences.
        self.provider = os.getenv("STASH_PROVIDER", "anthropic")
        self.model = os.getenv("STASH_MODEL", "claude")
        self.prompt_version = os.getenv("STASH_PROMPT_VERSION", "stash-cards-v1")
        self.max_output_tokens = _env_int("STASH_MAX_OUTPUT_TOKENS", 3000)
        self.temperature = _env_float("STASH_TEMPERATURE", 0.2)

        # Chunking.
        self.chunk_target_chars = _env_int("STASH_CHUNK_TARGET_CHARS", 1700)
        self.chunk_max_chars = _env_int("STASH_CHUNK_MAX_CHARS", 2200)

        # Per-user daily AI token cap (soft-limit; exceeded uploads are queued
        # but the worker holds them until the next day instead of failing).
        self.daily_token_cap = _env_int("STASH_DAILY_TOKEN_CAP", 250_000)

        # Job worker tuning.
        self.worker_poll_seconds = _env_float("STASH_WORKER_POLL_SECONDS", 3.0)
        self.reap_after_seconds = _env_int("STASH_REAP_AFTER_SECONDS", 300)
        self.heartbeat_seconds = _env_int("STASH_HEARTBEAT_SECONDS", 30)
        self.job_max_attempts = _env_int("STASH_JOB_MAX_ATTEMPTS", 3)

        # Storage root for uploaded source files (ephemeral on Render free tier,
        # so document text lives in the database; the file backup is optional).
        self.upload_dir = os.getenv(
            "STASH_UPLOAD_DIR",
            os.path.join(os.path.dirname(_PKG_DIR), "..", "Database", "stash_uploads"),
        )
        self.keep_source_files = os.getenv("STASH_KEEP_SOURCE_FILES", "1") not in (
            "0", "false", "no"
        )


StashConfig = StashConfig()  # noqa: E305  (module-level singleton)


def now_iso():
    """UTC timestamp in the same format SQLite's datetime('now') produces.

    Using one shared format for both backends keeps run_after/heartbeat_at
    comparisons working on SQLite and Postgres regardless of column type.
    """
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")