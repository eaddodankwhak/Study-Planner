"""One-time migration: on-disk uploads -> the durable file store.

Before the file store existed, uploaded bytes were written to the host
filesystem (Database/profile_uploads, Database/uploads, Database/stash_uploads,
Database/ai_uploads) while the database only kept a filename. This copies those
bytes into the file_blobs table so existing uploads keep working after the move.

Safe to re-run: a blob that is already present is skipped, so it is idempotent.
Nothing is deleted from disk, so you can still roll back by setting
FILE_STORAGE_BACKEND=local.

Usage:
    python migrate_uploads_to_db.py          # from the Backend directory
    python migrate_uploads_to_db.py --dry    # report only, change nothing
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "Database"))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import db  # noqa: E402
import storage  # noqa: E402

DRY = "--dry" in sys.argv

LEGACY_DIRS = {
    "profile": os.path.join(DATABASE_DIR, "profile_uploads"),
    "materials": os.path.join(DATABASE_DIR, "uploads"),
    "stash": os.path.join(DATABASE_DIR, "stash_uploads"),
    "ai": os.path.join(DATABASE_DIR, "ai_uploads"),
}

_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "zip": "application/zip",
    "mp4": "video/mp4",
}


def _mime(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _MIME_BY_EXT.get(ext, "application/octet-stream")


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _import(key, path, user_id, label):
    """Copy one file into the store unless it is already there."""
    if storage.exists(key):
        return "skip"
    try:
        data = _read(path)
    except OSError as exc:
        return f"error ({exc})"
    if not DRY:
        storage.save(
            key,
            data,
            user_id=user_id,
            filename=os.path.basename(key),
            content_type=_mime(key),
        )
    return "import"


def migrate_profiles():
    root = LEGACY_DIRS["profile"]
    moved = 0
    if not os.path.isdir(root):
        return 0
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        stem, ext = os.path.splitext(name)
        result = _import(
            storage.profile_key(stem, ext.lstrip(".")),
            path,
            user_id=stem,
            label=name,
        )
        print(f"  profile {name}: {result}")
        moved += result == "import"
    return moved


def migrate_materials():
    root = LEGACY_DIRS["materials"]
    moved = 0
    if not os.path.isdir(root):
        return 0
    for slug in sorted(os.listdir(root)):
        folder = os.path.join(root, slug)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            result = _import(
                storage.material_key(slug, name), path, user_id=None, label=name
            )
            print(f"  material {slug}/{name}: {result}")
            moved += result == "import"
    return moved


def migrate_stash():
    """Stash's on-disk layout is <user_id>/<doc_id>.<ext>, which is already the
    storage key, so this is a straight copy."""
    root = LEGACY_DIRS["stash"]
    moved = 0
    if not os.path.isdir(root):
        return 0
    for user_id in sorted(os.listdir(root)):
        folder = os.path.join(root, user_id)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            ext = name.rsplit(".", 1)[-1] if "." in name else "bin"
            result = _import(
                storage.stash_key(user_id, name.rsplit(".", 1)[0], ext),
                path,
                user_id=user_id,
                label=name,
            )
            print(f"  stash {user_id}/{name}: {result}")
            moved += result == "import"
    return moved


def migrate_ai_materials():
    root = LEGACY_DIRS["ai"]
    moved = 0
    if not os.path.isdir(root):
        return 0
    for user_id in sorted(os.listdir(root)):
        folder = os.path.join(root, user_id)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            material_id = name.rsplit(".", 1)[0]
            result = _import(
                storage.ai_material_key(user_id, material_id),
                path,
                user_id=user_id,
                label=name,
            )
            print(f"  ai {user_id}/{name}: {result}")
            moved += result == "import"
    return moved


def main():
    if storage.backend_name() != "database":
        print("FILE_STORAGE_BACKEND is not 'database'; nothing to migrate.")
        return 0
    db.init_db()
    print(f"Database: {db.DB_PATH}")
    print(f"Mode: {'DRY RUN' if DRY else 'importing'}")
    total = 0
    for label, fn in (
        ("profile photos", migrate_profiles),
        ("course materials", migrate_materials),
        ("stash sources", migrate_stash),
        ("ai materials", migrate_ai_materials),
    ):
        print(f"{label}:")
        total += fn()
    verb = "would import" if DRY else "imported"
    print(f"Done: {verb} {total} file(s). Originals left on disk.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
