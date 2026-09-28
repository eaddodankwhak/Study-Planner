"""Background job worker for Stash.

A single in-process daemon thread claims queued jobs, heartbeats while working,
reaps jobs whose heartbeats have gone stale (post-crash recovery), and retries
transient failures with backoff.  Permanent failures stop the job — the
document already carries a user-safe error message.
"""

import os
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from . import repository  # noqa: E402
from .config import StashConfig  # noqa: E402
from .service import (  # noqa: E402
    PermanentError,
    TransientError,
    process_document,
)

_worker_thread = None
_stop = False


def ensure_worker():
    """Start the worker thread if it isn't already running."""
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_thread = threading.Thread(
        target=_run_loop, name="stash-worker", daemon=True
    )
    _worker_thread.start()


def stop_worker():
    global _stop
    _stop = True


def _run_loop():
    while not _stop:
        try:
            _tick()
        except Exception:  # noqa: BLE001 - a bad tick must not kill the loop
            pass
        time.sleep(StashConfig.worker_poll_seconds)


def _tick():
    repository.requeue_stale_jobs()
    job = repository.claim_job()
    if not job:
        return

    stop_evt = threading.Event()

    def _heartbeat():
        while not stop_evt.is_set():
            repository.heartbeat_job(job["id"])
            stop_evt.wait(StashConfig.heartbeat_seconds)

    hb = threading.Thread(target=_heartbeat, name="stash-hb", daemon=True)
    hb.start()
    try:
        if job["job_type"] == "process_document":
            process_document(job["document_id"])
            repository.update_job(job["id"], status="done")
        else:
            raise ValueError(f"unknown job type: {job['job_type']}")
    except PermanentError:
        # Terminal for this document: the service already wrote the reason.
        repository.update_job(job["id"], status="done", error="permanent")
    except (TransientError, Exception) as exc:  # noqa: BLE001
        attempts = job.get("attempts") or 0
        if attempts >= (job.get("max_attempts") or StashConfig.job_max_attempts):
            repository.update_job(job["id"], status="error", error=str(exc)[:500])
        else:
            repository.update_job(job["id"], status="queued", error=str(exc)[:500])
            repository.delay_job(job["id"], 30 * attempts)
    finally:
        stop_evt.set()
        hb.join(timeout=1)