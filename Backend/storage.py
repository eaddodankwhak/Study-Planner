"""Durable storage for uploaded files.

Every uploaded byte in the app (profile photos, course materials, Stash source
documents, AI Hub extracted text) goes through this module instead of straight
to the filesystem. The host disk is ephemeral -- Render throws it away on every
deploy -- so keeping bytes on it meant a redeploy left the database pointing at
files that no longer existed: broken avatars and 404 downloads.

The default backend keeps bytes in the ``file_blobs`` table next to the rows that
reference them, so a file written on one device, served by any gunicorn worker,
is still there after a redeploy. Set ``FILE_STORAGE_BACKEND=local`` to go back to
writing under ``FILE_STORAGE_DIR`` instead, which is what a laptop wants.

Callers address files by a logical *key* -- the same relative path the
filesystem layout used ("profile/<user_id>.png", "uploads/<slug>/notes.pdf",
"<user_id>/<doc_id>.pdf"). Keys are storage-independent, so moving to S3 or
Cloudflare R2 later only means implementing a backend here; no call site changes.
"""

import hashlib
import os

import db

# Logical namespaces. Keeping them in one place stops the keys drifting apart
# between the writer and the reader.
PROFILE_PREFIX = "profile/"
MATERIALS_PREFIX = "uploads/"
STASH_PREFIX = "stash/"
AI_MATERIAL_PREFIX = "ai-materials/"


def backend_name():
    """Which storage backend is active: "database" (default) or "local"."""
    return (os.getenv("FILE_STORAGE_BACKEND") or "database").strip().lower()


def local_root():
    """Root directory for the "local" backend."""
    configured = os.getenv("FILE_STORAGE_DIR")
    if configured:
        return os.path.abspath(configured)
    # Default sits next to the other Database/ trees so local dev is unchanged.
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "Database", "uploads"))


def profile_key(user_id, extension):
    """Key for a user's profile photo. The filename matches the historical
    ``users.avatar_path`` value so that column keeps working unchanged."""
    return f"{PROFILE_PREFIX}{user_id}.{extension}"


def avatar_filename_from_key(key):
    """The bare filename a profile key implies, e.g. "u1.png"."""
    if not key:
        return None
    return key.rsplit("/", 1)[-1]


def material_key(slug, filename):
    return f"{MATERIALS_PREFIX}{slug}/{filename}"


def material_prefix(slug):
    return f"{MATERIALS_PREFIX}{slug}/"


def stash_key(user_id, doc_id, ext):
    return f"{STASH_PREFIX}{user_id}/{doc_id}.{ext}"


def ai_material_key(user_id, material_id, ext="txt"):
    return f"{AI_MATERIAL_PREFIX}{user_id}/{material_id}.{ext}"


def _sha256(data):
    return hashlib.sha256(data or b"").hexdigest()


def save(key, data, *, user_id=None, filename=None, content_type=None):
    """Persist ``data`` under ``key`` and return the key.

    Re-saving the same key replaces the bytes, which is what uploading a new
    profile photo should do.
    """
    data = bytes(data or b"")
    if backend_name() == "local":
        return _save_local(key, data)
    db.save_file_blob(
        key,
        data,
        user_id=user_id,
        filename=filename or avatar_filename_from_key(key),
        content_type=content_type,
        sha256=_sha256(data),
    )
    return key


def load(key):
    """Return the stored bytes for ``key``, or None when nothing is stored.

    This is the call that makes a file readable from any device: it comes from
    the database rather than a worker's local disk.
    """
    if not key:
        return None
    if backend_name() == "local":
        return _load_local(key)
    row = db.get_file_blob(key)
    return row["content"] if row else None


def load_meta(key):
    """Return {content, filename, content_type, size_bytes} or None.

    Preferred over :func:`load` when the response needs a filename or MIME type
    (downloads, inline previews).
    """
    if not key:
        return None
    if backend_name() == "local":
        data = _load_local(key)
        if data is None:
            return None
        return {
            "content": data,
            "filename": avatar_filename_from_key(key),
            "content_type": None,
            "size_bytes": len(data),
        }
    return db.get_file_blob(key)


def exists(key):
    if not key:
        return False
    if backend_name() == "local":
        return os.path.isfile(_local_path(key))
    return db.file_blob_exists(key)


def delete(key):
    if not key:
        return
    if backend_name() == "local":
        path = _local_path(key)
        if os.path.isfile(path):
            os.remove(path)
        return
    db.delete_file_blob(key)


def list_keys(prefix=""):
    if backend_name() == "local":
        root = local_root()
        base = root
        if prefix:
            base = os.path.join(root, prefix.replace("/", os.sep))
        out = []
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                out.append(rel)
        return sorted(out)
    return db.list_file_blob_keys(prefix)


def delete_prefix(prefix):
    """Remove everything under ``prefix`` (used when a subject is cleared)."""
    if not prefix:
        return
    for key in list_keys(prefix):
        delete(key)


# ----------------------------------------------------------------- local

def _local_path(key):
    """Resolve a logical key inside the local root, refusing to escape it.

    Keys are built from user ids, subject slugs and sanitised filenames, but this
    is the one place that turns a key into a real path, so it is where a
    traversal attempt gets stopped.
    """
    root = local_root()
    path = os.path.abspath(os.path.join(root, key.replace("/", os.sep)))
    if path != root and not path.startswith(root + os.sep):
        raise ValueError(f"refusing to resolve key outside storage root: {key!r}")
    return path


def _save_local(key, data):
    path = _local_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return key


def _load_local(key):
    path = _local_path(key)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()
