"""Upload security and storage for Stash.

Validates size limits and real file type via magic bytes (never trusts the
client's extension alone), computes a SHA-256 for duplicate detection, and
stores uploads under a per-user, per-document key with a random file name so
nothing user-controlled touches the path directly.

Bytes go through the durable store at the Backend root (``file_store``) rather
than the host filesystem, which is ephemeral on Render: a source file uploaded
before a redeploy is still readable after it.
"""

import hashlib
import io
import os
import sys

from werkzeug.utils import secure_filename

# Make the Backend root importable so `storage` resolves (this package lives at
# Backend/stash/ and is imported as `stash.security`).
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

import storage as file_store  # noqa: E402

from .config import StashConfig


class StashSecurityError(Exception):
    """Raised when an upload is rejected before any work happens."""


#: Allowed Stash source formats. ext -> detection hints checked in order.
ALLOWED_EXTS = {"pdf", "pptx"}

_PDF_HEADER = b"%PDF-"


def info():
    """Expose the current limits so the upload page can render them."""
    return {
        "maxFileBytes": StashConfig.max_file_bytes,
        "maxPdfPages": StashConfig.max_pdf_pages,
        "maxPptxSlides": StashConfig.max_pptx_slides,
        "allowedExts": sorted(ALLOWED_EXTS),
    }


def read_upload(stream, max_bytes=None):
    """Read an uploaded file into memory, enforcing the byte cap.

    Streaming in fixed chunks prevents a malicious Content-Length from being
    the only gate: the cap is enforced on actual bytes read.
    """
    max_bytes = max_bytes or StashConfig.max_file_bytes
    chunks = []
    total = 0
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise StashSecurityError(
                f"File is larger than the {_human(max_bytes)} limit."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def classify(filename, data):
    """Return (ext, kind) for validated content, raising on unsupported input."""
    name = (filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in ALLOWED_EXTS:
        raise StashSecurityError(
            "Unsupported file type. Stash accepts PDF and PPTX."
        )
    if ext == "pdf":
        if not data.startswith(_PDF_HEADER):
            raise StashSecurityError("That file is not a valid PDF.")
        return "pdf", "pdf"
    # pptx
    if not _looks_like_pptx(data):
        raise StashSecurityError("That file is not a valid PPTX (Open XML) deck.")
    return "pptx", "pptx"


def _looks_like_pptx(data):
    try:
        import zipfile

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            return "[Content_Types].xml" in names and any(
                n.startswith("ppt/slides/slide") and n.endswith(".xml")
                for n in names
            )
    except Exception:  # noqa: BLE001 - a corrupt or malicious zip must not crash
        return False


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def safe_title(filename):
    """Derive a reader-friendly document title from the uploaded file name."""
    name = secure_filename(filename or "document")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = stem.replace("_", " ").replace("-", " ").strip()
    return (stem[:80] or "Untitled document")


def path_for(user_id, doc_id, ext):
    """The user-scoped storage key and legacy on-disk path for a source file.

    The key (``<user_id>/<doc_id>.<ext>``) is what goes in
    ``stash_documents.storage_key`` and is unchanged by the move to the durable
    store, so existing rows keep working. The path is only used by the local
    storage backend.
    """
    # Forward slashes always: this key is a storage identifier, not a native
    # path, and must compare equal across Windows and Linux.
    key = f"{user_id}/{doc_id}.{ext}"
    return key, os.path.join(StashConfig.upload_dir, key.replace("/", os.sep))


def save_source(data, user_id, doc_id, ext):
    """Persist an upload; returns the storage key (user-scoped path)."""
    key = file_store.stash_key(user_id, doc_id, ext)
    file_store.save(
        key,
        data,
        user_id=user_id,
        filename=f"{doc_id}.{ext}",
    )
    # Keep the historical key shape (forward-slashed "<user_id>/<doc_id>.<ext>")
    # so stash_documents.storage_key stays comparable with rows written before
    # this change.
    return f"{user_id}/{doc_id}.{ext}"


def load_source(storage_key):
    """Return the raw bytes of a stored source file, or None."""
    if not storage_key:
        return None
    return file_store.load(_blob_key(storage_key))


def delete_source(storage_key):
    if not storage_key:
        return
    file_store.delete(_blob_key(storage_key))


def _blob_key(storage_key):
    """Translate a stored Stash key into the durable store's key.

    Tolerates a key that is already fully qualified so re-saving is idempotent.
    """
    key = storage_key.replace("\\", "/")
    if key.startswith(file_store.STASH_PREFIX):
        return key
    return f"{file_store.STASH_PREFIX}{key}"


def _human(size):
    mb = size / (1024 * 1024)
    if mb >= 1:
        return f"{mb:.0f} MB"
    return f"{size // 1024} KB"