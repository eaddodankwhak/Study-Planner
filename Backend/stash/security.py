"""Upload security and storage for Stash.

Validates size limits and real file type via magic bytes (never trusts the
client's extension alone), computes a SHA-256 for duplicate detection, and
stores uploads under a per-user, per-document path with a random file name so
nothing user-controlled touches the filesystem directly.
"""

import hashlib
import io
import os

from werkzeug.utils import secure_filename

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
    user_dir = os.path.join(StashConfig.upload_dir, user_id)
    return user_dir, os.path.join(user_dir, f"{doc_id}.{ext}")


def save_source(data, user_id, doc_id, ext):
    """Persist an upload; returns the storage key (user-scoped path)."""
    user_dir, full = path_for(user_id, doc_id, ext)
    os.makedirs(user_dir, exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data)
    return os.path.join(user_id, f"{doc_id}.{ext}")


def load_source(storage_key):
    """Return the raw bytes of a stored source file, or None."""
    if not storage_key:
        return None
    full = os.path.join(StashConfig.upload_dir, storage_key)
    if not os.path.isfile(full):
        return None
    try:
        with open(full, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def delete_source(storage_key):
    if not storage_key:
        return
    full = os.path.join(StashConfig.upload_dir, storage_key)
    try:
        os.remove(full)
    except OSError:
        pass


def _human(size):
    mb = size / (1024 * 1024)
    if mb >= 1:
        return f"{mb:.0f} MB"
    return f"{size // 1024} KB"