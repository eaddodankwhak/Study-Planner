"""Study-material file handling for the AI Learning Hub.

Supports uploading and extracting text from common study materials:
  - text:         txt, md, csv, json, log
  - pdf:          PyPDF2 if installed (graceful message otherwise)
  - docx:         python-docx if installed (graceful message otherwise)
  - images:       Dimensions/format only via Pillow if installed; OCR is not
                  performed to avoid sending binary blobs or huge contexts.

Referenced limits come from ai/limits.py to keep maximum sizes consistent.
"""

import io
import os
import uuid

from werkzeug.utils import secure_filename

from . import limits

ALLOWED_TEXT_EXTS = {"txt", "md", "csv", "json", "log", "py", "c", "cpp", "js", "html", "css"}

#: Recognized categories -> what we can do with them.
SUPPORTED = {
    "text": "text",
    "pdf": "pdf",
    "docx": "docx",
    "image": "image",
}


def _has_module(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def extract_text(filename, content: bytes, original_name=""):
    """Return {"kind":..., "text":..., "note":...} for uploaded content.

    kind is one of "text", "pdf", "docx", "image", "unsupported".
    text may be empty for image/unsupported (or when a library is missing).
    """
    name = (original_name or filename).lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""

    if ext in ALLOWED_TEXT_EXTS:
        return {"kind": "text", "text": _decode(content), "note": ""}

    if ext == "pdf":
        if not _has_module("PyPDF2"):
            return {
                "kind": "pdf",
                "text": "",
                "note": "PDF preview requires the 'PyPDF2' package, which is not installed. "
                        "You can still ask text-based questions without the PDF.",
            }
        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(content))
            pages = [p.extract_text() or "" for p in reader.pages]
            return {"kind": "pdf", "text": "\n\n".join(pages), "note": ""}
        except Exception as exc:  # noqa: BLE001
            return {"kind": "pdf", "text": "", "note": f"Could not read that PDF: {exc}"}

    if ext == "docx":
        if not _has_module("docx"):
            return {
                "kind": "docx",
                "text": "",
                "note": "DOCX preview requires the 'python-docx' package, which is not installed.",
            }
        try:
            import docx
            doc = docx.Document(io.BytesIO(content))
            paras = [p.text for p in doc.paragraphs if p.text.strip()]
            return {"kind": "docx", "text": "\n".join(paras), "note": ""}
        except Exception as exc:  # noqa: BLE001
            return {"kind": "docx", "text": "", "note": f"Could not read that DOCX: {exc}"}

    if ext in {"png", "jpg", "jpeg", "gif", "webp", "bmp"}:
        if not _has_module("PIL"):
            return {
                "kind": "image",
                "text": "",
                "note": "Image preview requires the 'Pillow' package, which is not installed.",
            }
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(content))
            return {
                "kind": "image",
                "text": "",
                "note": f"Attached image ({img.format}, {img.width}x{img.height}). "
                        "Image content is not OCR'd automatically; describe it or ask a question about it.",
            }
        except Exception as exc:  # noqa: BLE001
            return {"kind": "image", "text": "", "note": f"Could not read that image: {exc}"}

    return {"kind": "unsupported", "text": "", "note": "That file type is not supported for AI analysis."}


def _decode(content: bytes):
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def truncate_for_context(text, max_chars=None):
    """Trim extracted material to the context budget; return a prompt snippet."""
    max_chars = max_chars or limits.MAX_MATERIAL_CHARS
    if text is None:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"
