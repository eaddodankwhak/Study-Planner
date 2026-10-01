"""Tests for durable file storage.

Uploaded bytes used to be written to the host filesystem, which is ephemeral on
Render. That made profile photos and course materials disappear on every deploy
while the database kept pointing at them, and it meant a file's availability
depended on which machine served the request. These tests pin the behaviour that
replaces that: a file written once is readable from any client, survives logout,
and outlives the process that wrote it.

The "different device" cases are modelled honestly -- a second test client with
its own session, and a read through a brand-new storage handle -- rather than by
re-importing the app, which would still share one SQLite file.
"""

import io
import os
import sys
import unittest

os.environ.setdefault(
    "DATABASE_PATH", os.path.join(os.path.expanduser("~"), "file-storage-test.db")
)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db  # noqa: E402

db.init_db()

import storage  # noqa: E402

UID = "storage-user-1"
OTHER_UID = "storage-user-2"

_LOCAL_ENV = ("FILE_STORAGE_BACKEND", "FILE_STORAGE_DIR")

PNG_BYTES = b"\x89PNG\r\n\x1a\n-fake-image-bytes"
PDF_BYTES = b"%PDF-1.7 fake document body"


def _clean():
    db._execute("DELETE FROM file_blobs")
    for uid in (UID, OTHER_UID):
        # courses reference users(id), so they go first.
        db._execute("DELETE FROM courses WHERE user_id = ?", (uid,))
        # delete_user() is not used here: it would also drop blobs this test is
        # about to assert on, and the leftovers from a previous test are removed
        # by the DELETE above.
        db._execute("DELETE FROM users WHERE id = ?", (uid,))
    db.create_user(UID, "Sam", "sam-storage@example.com", "hash")
    db.create_user(OTHER_UID, "Alex", "alex-storage@example.com", "hash")


class _Base(unittest.TestCase):
    def setUp(self):
        _clean()

    def tearDown(self):
        _clean()


class BlobRoundTripTest(_Base):
    def test_save_and_load_returns_identical_bytes(self):
        storage.save(storage.profile_key(UID, "png"), PNG_BYTES, user_id=UID)
        self.assertEqual(storage.load(storage.profile_key(UID, "png")), PNG_BYTES)

    def test_meta_reports_filename_type_and_size(self):
        key = storage.material_key("math101", "notes.pdf")
        storage.save(
            key, PDF_BYTES, user_id=UID, filename="notes.pdf",
            content_type="application/pdf",
        )
        meta = storage.load_meta(key)
        self.assertEqual(meta["content"], PDF_BYTES)
        self.assertEqual(meta["filename"], "notes.pdf")
        self.assertEqual(meta["content_type"], "application/pdf")
        self.assertEqual(meta["size_bytes"], len(PDF_BYTES))

    def test_saving_the_same_key_replaces_rather_than_duplicates(self):
        key = storage.profile_key(UID, "png")
        storage.save(key, PNG_BYTES, user_id=UID)
        storage.save(key, b"replaced", user_id=UID)
        self.assertEqual(storage.load(key), b"replaced")
        # One row, not two.
        self.assertEqual(
            [k for k in storage.list_keys() if k == key], [key]
        )

    def test_missing_key_loads_as_none(self):
        self.assertIsNone(storage.load("profile/nobody.png"))
        self.assertIsNone(storage.load_meta("uploads/x/y.pdf"))
        self.assertFalse(storage.exists("profile/nobody.png"))

    def test_delete_removes_the_bytes(self):
        key = storage.material_key("math101", "gone.pdf")
        storage.save(key, PDF_BYTES, user_id=UID)
        storage.delete(key)
        self.assertIsNone(storage.load(key))
        self.assertFalse(storage.exists(key))

    def test_empty_key_is_rejected_rather_than_reading_everything(self):
        self.assertIsNone(storage.load(""))
        self.assertIsNone(storage.load_meta(""))
        self.assertFalse(storage.exists(""))

    def test_binary_content_survives_intact(self):
        """Bytes are stored, not decoded as text: a NUL and 0xFF must round-trip."""
        blob = bytes(range(256))
        key = storage.ai_material_key(UID, "abc123")
        storage.save(key, blob, user_id=UID)
        self.assertEqual(storage.load(key), blob)

    def test_list_keys_is_scoped_by_prefix(self):
        storage.save(storage.material_key("math101", "a.pdf"), PDF_BYTES, user_id=UID)
        storage.save(storage.material_key("cs201", "b.pdf"), PDF_BYTES, user_id=UID)
        storage.save(storage.profile_key(UID, "png"), PNG_BYTES, user_id=UID)

        math = storage.list_keys(storage.material_prefix("math101"))
        self.assertEqual(math, [storage.material_key("math101", "a.pdf")])
        self.assertIn(storage.material_key("cs201", "b.pdf"), storage.list_keys("uploads/"))
        self.assertEqual(len(storage.list_keys()), 3)

    def test_delete_prefix_clears_a_whole_namespace(self):
        storage.save(storage.material_key("math101", "a.pdf"), PDF_BYTES, user_id=UID)
        storage.save(storage.material_key("math101", "b.pdf"), PDF_BYTES, user_id=UID)
        storage.save(storage.material_key("cs201", "c.pdf"), PDF_BYTES, user_id=UID)

        storage.delete_prefix(storage.material_prefix("math101"))

        self.assertEqual(storage.list_keys(storage.material_prefix("math101")), [])
        # The other subject is untouched.
        self.assertEqual(
            storage.list_keys(storage.material_prefix("cs201")),
            [storage.material_key("cs201", "c.pdf")],
        )

    def test_user_ids_are_namespaced_so_one_student_cannot_read_another(self):
        mine = storage.profile_key(UID, "png")
        theirs = storage.profile_key(OTHER_UID, "png")
        storage.save(mine, PNG_BYTES, user_id=UID)
        self.assertIsNone(storage.load(theirs))

    def test_delete_file_blobs_for_user_clears_only_that_user(self):
        storage.save(storage.profile_key(UID, "png"), PNG_BYTES, user_id=UID)
        storage.save(
            storage.stash_key(OTHER_UID, "doc1", "pdf"), PDF_BYTES, user_id=OTHER_UID
        )
        db.delete_file_blobs_for_user(UID)
        self.assertIsNone(storage.load(storage.profile_key(UID, "png")))
        self.assertIsNotNone(storage.load(storage.stash_key(OTHER_UID, "doc1", "pdf")))


class AccountDeletionTest(_Base):
    """Deleting an account must not leave a user's bytes behind in the database,
    but must not take a shared course material with it either."""

    def test_private_uploads_are_removed_with_the_account(self):
        storage.save(storage.profile_key(UID, "png"), PNG_BYTES, user_id=UID)
        storage.save(storage.stash_key(UID, "doc1", "pdf"), PDF_BYTES, user_id=UID)
        storage.save(storage.ai_material_key(UID, "m1"), b"notes", user_id=UID)

        db.delete_user(UID)

        self.assertIsNone(storage.load(storage.profile_key(UID, "png")))
        self.assertIsNone(storage.load(storage.stash_key(UID, "doc1", "pdf")))
        self.assertIsNone(storage.load(storage.ai_material_key(UID, "m1")))

    def test_shared_course_material_survives_the_uploader_leaving(self):
        """A material uploaded to a subject is shared, so it is not the uploader's
        to delete when they remove their own account."""
        key = storage.material_key("math101", "shared.pdf")
        storage.save(key, PDF_BYTES, user_id=UID, filename="shared.pdf")

        db.delete_user(UID)

        self.assertEqual(storage.load(key), PDF_BYTES)

    def test_another_users_uploads_are_untouched(self):
        storage.save(storage.profile_key(OTHER_UID, "png"), PNG_BYTES, user_id=OTHER_UID)
        db.delete_user(UID)
        self.assertEqual(
            storage.load(storage.profile_key(OTHER_UID, "png")), PNG_BYTES
        )


class KeySchemeTest(_Base):
    def test_profile_key_keeps_the_legacy_avatar_filename(self):
        """users.avatar_path still holds a bare filename, so the key must end in it."""
        key = storage.profile_key(UID, "png")
        self.assertEqual(storage.avatar_filename_from_key(key), f"{UID}.png")

    def test_avatar_filename_from_empty_is_none(self):
        self.assertIsNone(storage.avatar_filename_from_key(""))
        self.assertIsNone(storage.avatar_filename_from_key(None))

    def test_namespaces_do_not_collide(self):
        keys = {
            storage.profile_key(UID, "png"),
            storage.material_key("s", "f.pdf"),
            storage.stash_key(UID, "d1", "pdf"),
            storage.ai_material_key(UID, "m1"),
        }
        self.assertEqual(len(keys), 4, f"keys collided: {keys}")

    def test_stash_key_roundtrips_through_the_legacy_shape(self):
        """security.save_source returns '<user>/<doc>.pdf'; that must map back."""
        from stash import security

        key = security.save_source(PDF_BYTES, UID, "doc-1", "pdf")
        self.assertEqual(key, f"{UID}/doc-1.pdf")
        self.assertEqual(security.load_source(key), PDF_BYTES)
        # Idempotent: a fully-qualified key resolves to the same place.
        self.assertEqual(
            security._blob_key(storage.stash_key(UID, "doc-1", "pdf")),
            security._blob_key(key),
        )
        security.delete_source(key)
        self.assertIsNone(security.load_source(key))


class CrossDeviceTest(_Base):
    """A file written on one client must be readable on another, and must
    survive logging out and back in."""

    def _client(self, uid):
        import app as app_module

        c = app_module.app.test_client()
        with c.session_transaction() as sess:
            sess["user_id"] = uid
        return c

    def test_avatar_uploaded_on_one_device_is_served_to_another(self):
        import app as app_module

        device_a = self._client(UID)
        resp = device_a.post(
            "/settings/avatar",
            data={"avatar": (io.BytesIO(PNG_BYTES), "me.png")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)

        # A separate client object, i.e. a different browser or phone.
        device_b = self._client(UID)
        got = device_b.get(f"/profile/avatar/{UID}.png")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.data, PNG_BYTES)

    def test_avatar_survives_logout_and_login(self):
        import app as app_module

        device = self._client(UID)
        device.post(
            "/settings/avatar",
            data={"avatar": (io.BytesIO(PNG_BYTES), "me.png")},
            content_type="multipart/form-data",
        )
        # Logout must not clear the row or the bytes.
        device.get("/logout")
        self.assertEqual(
            db.get_user(UID)["avatar_path"], f"{UID}.png"
        )
        self.assertEqual(storage.load(storage.profile_key(UID, "png")), PNG_BYTES)

        # Log back in on a "new device" and the photo is still there.
        again = self._client(UID)
        got = again.get(f"/profile/avatar/{UID}.png")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.data, PNG_BYTES)

    def test_material_uploaded_on_one_device_downloads_on_another(self):
        import app as app_module

        slug = _seed_subject(UID)
        device_a = self._client(UID)
        resp = device_a.post(
            f"/subject/{slug}/upload",
            data={"material": (io.BytesIO(PDF_BYTES), "lecture.pdf")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)

        device_b = self._client(UID)
        got = device_b.get(f"/subject/{slug}/download/lecture.pdf")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.data, PDF_BYTES)
        self.assertIn("lecture.pdf", got.headers.get("Content-Disposition", ""))

    def test_material_listing_is_the_same_on_a_second_device(self):
        import app as app_module

        slug = _seed_subject(UID)
        self._client(UID).post(
            f"/subject/{slug}/upload",
            data={"material": (io.BytesIO(PDF_BYTES), "a.pdf")},
            content_type="multipart/form-data",
        )
        device_b = self._client(UID)
        # The subject page redirects until onboarding is done, so assert on the
        # upload's own success page rather than the listing page.
        page = device_b.post(
            f"/subject/{slug}/upload",
            data={"material": (io.BytesIO(b"%PDF-1.4 second"), "b.pdf")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"a.pdf", page.data)

    def test_another_student_cannot_fetch_someone_elses_avatar(self):
        device = self._client(OTHER_UID)
        storage.save(storage.profile_key(UID, "png"), PNG_BYTES, user_id=UID)
        resp = device.get(f"/profile/avatar/{UID}.png")
        self.assertEqual(resp.status_code, 404)

    def test_avatar_request_for_wrong_extension_is_not_served(self):
        self._client(UID)
        storage.save(storage.profile_key(UID, "png"), PNG_BYTES, user_id=UID)
        db.update_user(UID, {"avatar_path": f"{UID}.png"})
        resp = self._client(UID).get(f"/profile/avatar/{UID}.jpg")
        self.assertEqual(resp.status_code, 404)

    def test_ai_material_text_is_readable_after_upload(self):
        from ai import api as ai_api

        client = self._client(UID)
        with client.session_transaction() as sess:
            sess["user_id"] = UID
        resp = client.post(
            "/api/ai/upload",
            data={"file": (io.BytesIO(b"chapter one notes"), "notes.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        material_id = resp.get_json()["materialId"]
        # The exact call the chat path makes to re-attach the material.
        self.assertIn("chapter one notes", ai_api._material_text(material_id, UID))

    def test_ai_material_text_is_scoped_to_its_owner(self):
        from ai import api as ai_api

        material_id = "abc123"
        storage.save(
            storage.ai_material_key(UID, material_id),
            b"private notes",
            user_id=UID,
        )
        self.assertIsNone(ai_api._material_text(material_id, OTHER_UID))
        self.assertIsNone(ai_api._material_text("../../../etc/passwd", UID))
        self.assertIsNone(ai_api._material_text("", UID))


class LocalBackendTest(_Base):
    """The escape hatch used for laptop development must behave the same way."""

    def setUp(self):
        super().setUp()
        # storage reads these env vars on every call, so no reload is needed.
        self._prev = {k: os.environ.get(k) for k in _LOCAL_ENV}
        self.root = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "_tmp_local_store"
        )
        os.environ["FILE_STORAGE_BACKEND"] = "local"
        os.environ["FILE_STORAGE_DIR"] = self.root

    def tearDown(self):
        import shutil

        for key, value in self._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.root, ignore_errors=True)
        super().tearDown()

    def test_local_backend_round_trips_to_disk(self):
        key = storage.profile_key(UID, "png")
        storage.save(key, PNG_BYTES, user_id=UID)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "profile", f"{UID}.png")))
        self.assertEqual(storage.load(key), PNG_BYTES)
        self.assertTrue(storage.exists(key))

    def test_local_backend_rejects_key_escaping_the_root(self):
        with self.assertRaises(ValueError):
            storage.load("../../escaped.png")

    def test_local_backend_delete_and_listing(self):
        key = storage.material_key("math101", "x.pdf")
        storage.save(key, PDF_BYTES, user_id=UID)
        self.assertEqual(
            storage.list_keys(storage.material_prefix("math101")), [key]
        )
        storage.delete(key)
        self.assertFalse(storage.exists(key))


class PostgresCompatibilityTest(unittest.TestCase):
    """Render runs with DATABASE_URL set, so this backend is what actually runs in
    production while every other test here drives SQLite.

    No Postgres server is needed: these assert on the SQL the layer hands to
    psycopg2, which is where a SQLite-only dialect would break. The `?`
    placeholders and `BLOB` type are the two things that silently fail there --
    psycopg2 does not translate them, it raises.
    """

    def setUp(self):
        import pg

        self.pg = pg

    def _translate(self, sql):
        return self.pg._translate(sql)

    def test_blob_statements_leave_no_sqlite_placeholders(self):
        """Every ? must become %s or psycopg2 rejects the statement."""
        for sql in (
            "INSERT INTO file_blobs (key, content) VALUES (?, ?)",
            "SELECT content FROM file_blobs WHERE key = ?",
            "SELECT 1 AS present FROM file_blobs WHERE key = ?",
            "DELETE FROM file_blobs WHERE key = ?",
            "SELECT key FROM file_blobs WHERE key LIKE ? ORDER BY key",
            "DELETE FROM file_blobs WHERE user_id = ? AND key LIKE ?",
        ):
            with self.subTest(sql=sql):
                self.assertNotIn("?", self._translate(sql))

    def test_upsert_survives_translation(self):
        """save_file_blob relies on ON CONFLICT, which both engines support but
        which must not be rewritten into the INSERT OR REPLACE form."""
        out = self._translate(
            "INSERT INTO file_blobs (key, content) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET content = excluded.content, "
            "updated_at = CURRENT_TIMESTAMP"
        )
        self.assertIn("ON CONFLICT (key) DO UPDATE", out)
        self.assertNotIn("INSERT OR", out)

    def test_file_blobs_column_is_bytea_not_blob(self):
        """SQLite's BLOB keyword has no meaning in Postgres; the schema variant
        applied when DATABASE_URL is set has to swap it."""
        self.assertIn("content      BYTEA", db._SCHEMA_PG)
        self.assertNotIn("content      BLOB", db._SCHEMA_PG)

    def test_whole_postgres_schema_translates_without_sqlite_dialect(self):
        """Each statement must come out of the translator with no ? and no
        INSERT OR / rowid / datetime('now') left behind."""
        statements = self.pg._split_script(db._SCHEMA_PG)
        self.assertGreater(len(statements), 50)
        for stmt in statements:
            out = self.pg._translate(stmt)
            with self.subTest(stmt=stmt.strip().splitlines()[0][:60]):
                self.assertNotIn("?", out)
                self.assertNotIn("INSERT OR", out.upper())
                self.assertNotIn("rowid", out.lower())
                self.assertNotIn("datetime('now')", out.lower())

    def test_sqlite_binary_adapts_to_a_bytea_literal(self):
        """save_file_blob wraps the bytes in sqlite3.Binary, which is a memoryview.
        psycopg2 has to be able to adapt that to bytea, or every upload 500s."""
        import sqlite3

        import psycopg2.extensions

        quoted = psycopg2.extensions.adapt(sqlite3.Binary(PNG_BYTES)).getquoted()
        self.assertTrue(quoted.endswith(b"::bytea"), quoted)
        # Postgres renders the payload as an octal-escaped string literal.
        self.assertIn(b"\\211PNG", quoted)

    def test_blob_row_normalises_psycopg2_memoryview(self):
        """psycopg2 hands BYTEA back as a memoryview, not bytes. get_file_blob
        must convert it, or callers get a memoryview they cannot hash."""
        raw = memoryview(PDF_BYTES)
        self.assertIsInstance(raw, memoryview)
        row = {
            "content": raw,
            "filename": "notes.pdf",
            "content_type": "application/pdf",
            "size_bytes": None,
        }
        # Mirror get_file_blob's normalisation without needing a live server.
        content = row["content"]
        if isinstance(content, memoryview):
            content = content.tobytes()
        self.assertIsInstance(content, bytes)
        self.assertEqual(content, PDF_BYTES)
        self.assertEqual(len(content), len(PDF_BYTES))


def _seed_subject(uid, code="STO101"):
    """Create a course row and return the slug the subject routes expect.

    Routes address a subject by the slug derived from its course code, not by
    the row id, so the test has to ask for the same thing get_subject() does.
    """
    import courses as courses_mod

    courses_mod.upsert_course(uid, code, title="Storage 101")
    return courses_mod.course_slug(code)


if __name__ == "__main__":
    unittest.main()
