"""Tests for the Stash feature (upload -> AI cards -> reading).

Suites:
- StashParsingTest  : PDF -> parse -> sections/chunks contract.
- StashSchemaTest   : generator schema validation + deterministic quiz.
- StashPipelineTest : service lifecycle with a fake provider (end-to-end
  generation, duplicates, privacy/provider/limit failures, regeneration).
- StashApiTest      : JSON API happy paths, authorization, duplicate 409.
- StashUploadTest   : upload security (type sniffing, size caps).

The in-process worker is disabled and every generated document's job is parked
("done") before synchronous processing so a worker thread started earlier by
another test module can never claim the same job mid-test.
"""

import io
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("STASH_DISABLE_WORKER", "1")
os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.mkdtemp(), "stash-test.db"))
os.environ.setdefault("STASH_UPLOAD_DIR", os.path.join(tempfile.mkdtemp(), "stash_up"))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db  # noqa: E402

db.init_db()

from app import app  # noqa: E402
import ai.models as model_registry  # noqa: E402
from stash import quiz, repository, schemas, security, service  # noqa: E402
from stash.ai import client, validator  # noqa: E402
from stash.parsing import build_structure, parse_document  # noqa: E402


def _esc_text(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(pages):
    out = b"%PDF-1.4\n"
    objects = []
    oid = 1
    catalog = ("<< /Type /Catalog /Pages %d 0 R >>\n" % 2)
    objects.append((oid, catalog.encode("latin-1")))
    catalog_id = oid
    oid += 1
    n_pages = len(pages)
    kids = " ".join(str(3 + 3 * i) + " 0 R" for i in range(n_pages))
    objects.append((oid, ("<< /Type /Pages /Kids [%s] /Count %d >>\n" % (kids, n_pages)).encode("latin-1")))
    oid += 1
    for text in pages:
        pid = oid
        font_id = oid + 1
        content_id = oid + 2
        objects.append((oid, ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
                        " /Resources << /Font << /F1 %d 0 R >> >>"
                        " /Contents %d 0 R >>\n" % (font_id, content_id)).encode("latin-1")))
        oid += 1
        objects.append((oid, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"))
        oid += 1
        lines = []
        for line in text.splitlines()[:60]:
            lines.append("(%s) Tj T*" % _esc_text(line))
        stream_body = ("BT /F1 12 Tf 50 740 Td 14 TL\n" + "\n".join(lines) + "\nET").encode("latin-1")
        objects.append((oid, b"<< /Length %d >>\nstream\n" % len(stream_body) + stream_body + b"\nendstream\n"))
        oid += 1
    offsets = {}
    for oid_, body in objects:
        offsets[oid_] = len(out)
        out += b"%d 0 obj\n" % oid_
        out += body
        out += b"endobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % oid
    out += b"0000000000 65535 f \n"
    for oid_ in range(1, oid):
        out += b"%010d 00000 n \n" % offsets[oid_]
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\n" % (oid, catalog_id)
    out += b"startxref\n%d\n%%%%EOF\n" % xref_pos
    return out


def _ok_card(title, body, type_="definition", **extra):
    card = {"type": type_, "title": title, "content": body, "confidence": 0.9}
    card.update(extra)
    return card


class FakeProvider:
    name = "anthropic"
    is_mock = False
    api_key = "fake"

    def __init__(self, mode="ok", usage=None):
        self.mode = mode
        self.usage = usage or {"inputTokens": 1200, "outputTokens": 800}

    def reply(self, prompt):
        import json as _json

        if self.mode == "badjson":
            return "This is not JSON at all."
        if "recap" in (prompt or "")[:400]:
            cards = [_ok_card(
                "Chapter recap",
                "This chapter ties together the key concepts that were introduced and explains the main steps clearly for revision purposes.",
                type_="recap", confidence=0.92)]
        else:
            cards = [
                _ok_card(
                    "Mitosis definition",
                    "Mitosis is the process by which a single cell divides into two genetically identical daughter cells ensuring growth and tissue repair along the way.",
                    "definition", key_term="Mitosis",
                    key_term_definition="Cell division producing two identical cells.",
                    source_page=1),
                _ok_card(
                    "Interphase dominates",
                    "Cells spend most of their cycle in interphase quietly preparing to divide which is why most dividing tissue looks calm under observation.",
                    "key_takeaway", confidence=0.7),
            ]
        return _json.dumps({"cards": cards})


PAGES_2_SECTIONS = [
    "Chapter One\nMitosis is the process by which a single cell divides into two genetically identical daughter cells ensuring growth and repair across the body.",
    "Chapter Two\nMitosis relies on precise chromosome alignment before the cell physically splits into two complete daughter cells.",
    "Chapter Three\nThe spindle apparatus separates the replicated chromosomes so each new cell receives one full set of genetic material.",
    "Chapter Four\nCytokinesis then cuts the cell in two, finishing the division and producing two viable independent daughter cells.",
    "Chapter Five\nEnvironmental signals regulate how often cells choose to enter division affecting growth and healing in the organism.",
    "Chapter Six\nA thorough discussion of how cell division ensures the organism stays healthy over time by replacing old damaged tissue quickly and carefully, while the new cells take on the exact role of the cells they replace, keeping every organ functioning through a lifetime of renewal and repair activity that continues long after the body has finished growing.",
]


_REAL_RESOLVE_GENERATION = client.resolve_generation
_REAL_GENERATE_JSON = client._generate_json
_REAL_PRIVACY_ALLOWS = client.privacy_allows
_REAL_USABLE_PROVIDERS = client.usable_providers

_ANTHROPIC_OPTION = {
    "provider": "anthropic",
    "providerName": "Claude",
    "model": "claude",
    "modelName": "Claude",
    "modelApiId": "claude-sonnet-4-5",
    "source": "server",
    "contextWindow": 200000,
    "maxOutput": 8192,
}


class StashTestBase(unittest.TestCase):
    UID_A = "stash-user-a"
    UID_B = "stash-user-b"

    def setUp(self):
        for uid in (self.UID_A, self.UID_B):
            db.delete_user(uid)
        db.create_user(self.UID_A, "Stash A", self.UID_A + "@example.com", "hash")
        db.create_user(self.UID_B, "Stash B", self.UID_B + "@example.com", "hash")

    def tearDown(self):
        client.resolve_generation = _REAL_RESOLVE_GENERATION
        client._generate_json = _REAL_GENERATE_JSON
        client.privacy_allows = _REAL_PRIVACY_ALLOWS
        client.usable_providers = _REAL_USABLE_PROVIDERS
        for uid in (self.UID_A, self.UID_B):
            db.delete_user(uid)

    def make_doc(self, uid=None, filename="sample.pdf", pages=None, blob=None):
        uid = uid or self.UID_A
        blob = blob if blob is not None else build_pdf(pages or PAGES_2_SECTIONS)
        doc = service.create_document(uid, filename, blob)
        self.park_job(doc)
        return doc, blob

    def park_job(self, doc):
        row = db._query_one(
            "SELECT id FROM stash_jobs WHERE document_id = ?", (doc["id"],)
        )
        if row:
            repository.update_job(row["id"], status="done")

    def patch_provider(self, mode="ok", usage=None, paid_by="server"):
        """Stub generation resolution + provider HTTP so the pipeline is hermetic.

        `paid_by` controls whether usage counts against the daily cap: the stub
        defaults to "server" so cap behaviour matches the existing suite.
        """
        fake = FakeProvider(mode=mode, usage=usage)
        model = dict(model_registry.get_model("claude"))
        model["model_id"] = "claude-3-5-sonnet"
        client.resolve_generation = lambda uid, preferred=None: {
            "provider": fake,
            "provider_id": "anthropic",
            "model": model,
            "model_id": "claude-3-5-sonnet",
            "paid_by": paid_by,
        }
        client._generate_json = (
            lambda provider, model_id, user_prompt, describe="": (
                (int(fake.usage["inputTokens"]), int(fake.usage["outputTokens"])),
                fake.reply(user_prompt),
            )
        )

    def client(self, uid=None):
        app.config["TESTING"] = True
        c = app.test_client()
        if uid:
            with c.session_transaction() as sess:
                sess["user_id"] = uid
        return c


class StashParsingTest(unittest.TestCase):
    def test_parse_pdf_normalizes_pages(self):
        blob = build_pdf(PAGES_2_SECTIONS)
        parsed = parse_document(blob, "pdf")
        self.assertEqual(parsed["page_count"], 6)
        self.assertEqual(len(parsed["pages"]), 6)
        self.assertIn("Mitosis", parsed["pages"][0]["text"])

    def test_build_structure_produces_sections_and_chunks(self):
        parsed = parse_document(build_pdf(PAGES_2_SECTIONS), "pdf")
        pages, structure = build_structure(parsed)
        self.assertEqual(len(pages), 6)
        self.assertGreaterEqual(len(structure["sections"]), 2)
        self.assertGreaterEqual(len(structure["chunks"]), 2)
        joined = " ".join(c["text"] for c in structure["chunks"])
        self.assertIn("Mitosis", joined)

    def test_build_structure_with_parseble_pdf_block(self):
        parsed = parse_document(build_pdf(PAGES_2_SECTIONS), "pdf")
        pages, structure = build_structure(parsed)
        for chunk in structure["chunks"]:
            self.assertIn("section_index", chunk)
            self.assertIn("page_start", chunk)
            self.assertIn("text", chunk)


class StashSchemaTest(unittest.TestCase):
    def test_shape_validation_accepts_a_valid_card(self):
        card = _ok_card(
            "Mitosis definition",
            "Mitosis is the process by which a single cell divides into two genetically identical daughter cells ensuring growth and repair.",
        )
        self.assertEqual(schemas.validate_card_shape(card), [])

    def test_shape_validation_rejects_short_body(self):
        card = _ok_card("Short body", "Mitosis divides cells.")
        self.assertTrue(any("too short" in p for p in schemas.validate_card_shape(card)))

    def test_shape_validation_rejects_unknown_type(self):
        card = _ok_card("Bad", "Mitosis is the process by which a single cell divides producing two identical daughter cells for growth.")
        card["type"] = "mystery"
        self.assertTrue(any("unknown card type" in p for p in schemas.validate_card_shape(card)))

    def test_shape_validation_rejects_non_dict(self):
        self.assertEqual(schemas.validate_card_shape(["nope"]), ["card is not an object"])

    def test_db_card_clamps_overlong_body(self):
        body = "word " * 100
        raw = _ok_card("Mitosis definition", body)
        card = schemas.to_db_card(raw)
        self.assertLessEqual(schemas.count_words(card["body"]), schemas.MAX_BODY_WORDS)

    def test_parse_cards_accepts_fenced_json_object(self):
        content = "Here you go:\n```json\n{\"cards\": [" + json.dumps(_ok_card(
            "Mitosis definition",
            "Mitosis is the process by which a single cell divides into two genetically identical daughter cells ensuring growth.",
        )) + "]}\n```"
        cards = validator.parse_cards(content, default_page=3)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["problems"], [])
        self.assertEqual(cards[0]["source_page_start"], 3)

    def test_parse_cards_raises_on_bad_json(self):
        with self.assertRaises(validator.GenerationError):
            validator.parse_cards("This is not JSON at all.")

    def test_content_hash_is_stable(self):
        a = validator.content_hash({"card_type": "definition", "title": "T", "body": "B"})
        b = validator.content_hash({"card_type": "definition", "title": "T", "body": "B"})
        c = validator.content_hash({"card_type": "definition", "title": "T", "body": "X"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


class StashQuizTest(StashTestBase):
    def _ready_doc(self):
        self.patch_provider()
        doc, _ = self.make_doc()
        service.process_document(doc["id"])
        return repository.get_document(doc["id"])

    def test_quiz_is_deterministic_and_quotes_real_cards(self):
        doc = self._ready_doc()
        self.assertEqual(doc["status"], "ready")
        first = quiz.build_quiz(doc["id"], max_options_char=2000)
        second = quiz.build_quiz(doc["id"], max_options_char=2000)
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first["questions"]), 2)
        bodies = {c["body"] for c in repository.list_cards(doc["id"], limit=100)}
        for q in first["questions"]:
            self.assertIsInstance(q["answer"], int)
            self.assertLess(q["answer"], len(q["options"]))
            self.assertIn(q["options"][q["answer"]], bodies)


class StashPipelineTest(StashTestBase):
    def setUp(self):
        super().setUp()
        self.patch_provider()

    def test_full_pipeline_generates_cards_and_metadata(self):
        doc, _ = self.make_doc()
        result = service.process_document(doc["id"])
        ready = repository.get_document(doc["id"])
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["model_used"], "claude-3-5-sonnet")
        self.assertEqual(ready["provider_used"], "anthropic")
        self.assertIsNone(ready.get("preferred_provider"))
        self.assertIsNone(ready.get("preferred_model"))
        self.assertEqual(ready["prompt_version"], "stash-cards-v1")
        self.assertEqual(ready["page_count"], 6)
        self.assertEqual(ready["progress_percent"], 100)
        self.assertEqual(repository.count_cards(doc["id"]), 6)
        sections = repository.list_sections(doc["id"])
        self.assertEqual(len(sections), 2)
        recaps = [
            c for c in repository.list_cards(doc["id"], limit=100)
            if c["card_type"] == "recap"
        ]
        self.assertEqual(len(recaps), 2)
        titles = {c["title"] for c in repository.list_cards(doc["id"], limit=100)}
        self.assertIn("Mitosis definition", titles)
        self.assertIn("Interphase dominates", titles)

    def test_usage_tokens_recorded_per_request(self):
        doc, _ = self.make_doc()
        service.process_document(doc["id"])
        usage = repository.today_usage(self.UID_A)
        self.assertEqual(usage["inputTokens"], 4800)
        self.assertEqual(usage["outputTokens"], 3200)

    def test_pipeline_is_idempotent_when_rerun(self):
        doc, _ = self.make_doc()
        service.process_document(doc["id"])
        service.process_document(doc["id"])
        ready = repository.get_document(doc["id"])
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(repository.count_cards(doc["id"]), 6)

    def test_duplicate_upload_raises_for_same_user(self):
        doc, blob = self.make_doc()
        with self.assertRaises(service.DuplicateDocumentError):
            service.create_document(self.UID_A, "copy.pdf", blob)

    def test_same_blob_allowed_for_different_user(self):
        doc, blob = self.make_doc()
        other = service.create_document(self.UID_B, "copy.pdf", blob)
        self.park_job(other)
        self.assertNotEqual(other["id"], doc["id"])
        self.assertEqual(other["user_id"], self.UID_B)

    def test_bad_generator_json_leaves_doc_incomplete(self):
        self.patch_provider(mode="badjson")
        doc, _ = self.make_doc()
        with self.assertRaises(service.PermanentError) as ctx:
            service.process_document(doc["id"])
        self.assertIn("Generation is incomplete", str(ctx.exception))
        failed = repository.get_document(doc["id"])
        self.assertEqual(failed["status"], "error")
        chunks = repository.list_chunks(doc["id"])
        self.assertTrue(all(c["status"] in ("error", "done") for c in chunks))
        self.assertGreaterEqual(
            repository.count_chunks(doc["id"]) - repository.count_done_chunks(doc["id"]), 1
        )

    def test_daily_limit_stops_pipeline_and_explains(self):
        original_cap = client.StashConfig.daily_token_cap
        client.StashConfig.daily_token_cap = 0
        try:
            doc, _ = self.make_doc()
            with self.assertRaises(service.PermanentError):
                service.process_document(doc["id"])
            limited = repository.get_document(doc["id"])
            self.assertEqual(limited["status"], "error")
            self.assertIn("Daily AI limit", limited["error_message"])
        finally:
            client.StashConfig.daily_token_cap = original_cap

    def test_privacy_toggle_blocks_generation(self):
        client.privacy_allows = lambda uid: False
        doc, _ = self.make_doc()
        with self.assertRaises(service.PermanentError):
            service.process_document(doc["id"])
        blocked = repository.get_document(doc["id"])
        self.assertEqual(blocked["status"], "error")
        self.assertIn("AI activity is turned off", blocked["error_message"])

    def test_no_provider_key_fails_loudly(self):
        def raiser(uid, preferred=None):
            raise client.NoProviderError(client.HINT)

        client.resolve_generation = raiser
        doc, _ = self.make_doc()
        with self.assertRaises(service.PermanentError):
            service.process_document(doc["id"])
        failed = repository.get_document(doc["id"])
        self.assertEqual(failed["status"], "error")
        self.assertIn("AI key", failed["error_message"])

    def test_regenerate_selected_section_then_process(self):
        doc, _ = self.make_doc()
        service.process_document(doc["id"])
        sections = repository.list_sections(doc["id"])
        target = sections[1]
        service.regenerate(doc["id"], section_indexes={target["position"]})
        requeued = repository.get_document(doc["id"])
        self.assertEqual(requeued["status"], "queued")
        self.assertEqual(repository.count_cards(doc["id"]), 6 - target["card_count"])
        service.process_document(doc["id"])
        refreshed = repository.list_sections(doc["id"])
        fresh_target = next(s for s in refreshed if s["id"] == target["id"])
        self.assertGreater(fresh_target["card_count"], 0)
        self.assertEqual(repository.get_document(doc["id"])["status"], "ready")
        self.assertEqual(repository.count_cards(doc["id"]), 6)

    def test_regenerate_unknown_section_raises(self):
        doc, _ = self.make_doc()
        service.process_document(doc["id"])
        with self.assertRaises(service.PermanentError):
            service.regenerate(doc["id"], section_indexes={99})


class StashProviderTest(StashTestBase):
    """Provider resolution: fixed key fallback order, env aliases, picker data."""

    _ENVS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_AI_API_KEY",
             "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "COPILOT_GITHUB_TOKEN",
             "GH_TOKEN")

    def setUp(self):
        super().setUp()
        self._saved_env = {name: os.environ.get(name) for name in self._ENVS}
        for name in self._ENVS:
            os.environ.pop(name, None)

    def tearDown(self):
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        super().tearDown()

    def _clear_keys(self):
        for name in self._ENVS:
            os.environ.pop(name, None)

    def test_server_fallback_order(self):
        os.environ["ANTHROPIC_API_KEY"] = "k"
        os.environ["GEMINI_API_KEY"] = "k"  # later alias must not win
        gen = client.resolve_generation(self.UID_A)
        self.assertEqual(gen["provider_id"], "anthropic")
        self.assertEqual(gen["model"]["id"], "claude")
        self.assertEqual(gen["paid_by"], "server")

        self._clear_keys()
        os.environ["OPENAI_API_KEY"] = "k"
        self.assertEqual(client.resolve_generation(self.UID_A)["provider_id"], "openai")

        self._clear_keys()
        os.environ["DEEPSEEK_API_KEY"] = "k"
        self.assertEqual(client.resolve_generation(self.UID_A)["provider_id"], "deepseek")

        self._clear_keys()
        os.environ["COPILOT_GITHUB_TOKEN"] = "k"
        self.assertEqual(client.resolve_generation(self.UID_A)["provider_id"], "copilot")

    def test_gemini_env_alias_is_accepted(self):
        os.environ["GEMINI_API_KEY"] = "k"
        gen = client.resolve_generation(self.UID_A)
        self.assertEqual(gen["provider_id"], "google")
        self.assertEqual(gen["model"]["id"], "gemini")
        self.assertEqual(gen["paid_by"], "server")

        self._clear_keys()
        os.environ["GOOGLE_AI_API_KEY"] = "k"
        self.assertEqual(client.resolve_generation(self.UID_A)["provider_id"], "google")

    def test_github_token_alias_is_accepted(self):
        os.environ["GH_TOKEN"] = "k"
        self.assertEqual(client.resolve_generation(self.UID_A)["provider_id"], "copilot")

    def test_mock_provider_is_never_usable(self):
        options = client.usable_providers(self.UID_A)
        self.assertEqual(options, [])
        with self.assertRaises(client.NoProviderError):
            client.resolve_generation(self.UID_A)

    def test_usable_providers_report_source_and_picker_fields(self):
        os.environ["OPENAI_API_KEY"] = "k"
        os.environ["DEEPSEEK_API_KEY"] = "k"
        options = client.usable_providers(self.UID_A)
        self.assertEqual(
            [o["provider"] for o in options], ["openai", "deepseek"]
        )
        entry = options[0]
        self.assertEqual(entry["source"], "server")
        self.assertEqual(entry["provider"], "openai")
        self.assertEqual(entry["model"], "gpt")
        self.assertIn("providerName", entry)
        self.assertIn("modelName", entry)
        self.assertIn("contextWindow", entry)
        self.assertIn("maxOutput", entry)

    def test_cap_applies_only_to_server_paid_generation(self):
        original_cap = client.StashConfig.daily_token_cap
        client.StashConfig.daily_token_cap = 0
        try:
            self.patch_provider(paid_by="personal")
            doc, _ = self.make_doc()
            service.process_document(doc["id"])  # personal keys are never capped
            ready = repository.get_document(doc["id"])
            self.assertEqual(ready["status"], "ready")
            server = repository.today_server_tokens(self.UID_A)
            self.assertEqual(server["inputTokens"], 0)
            self.assertEqual(server["outputTokens"], 0)
        finally:
            client.StashConfig.daily_token_cap = original_cap

    def test_describe_provider_availability_offers_picker_input(self):
        os.environ["OPENAI_API_KEY"] = "k"
        hints = client.describe_provider_availability(self.UID_A)
        self.assertTrue(hints["configured"])
        self.assertEqual(hints["providerId"], "openai")
        self.assertEqual(hints["modelId"], "gpt")
        self.assertEqual(len(hints["providers"]), 1)
        self.assertEqual(hints["providers"][0]["source"], "server")


class StashApiTest(StashTestBase):
    def test_unauthenticated_apis_return_401(self):
        c = self.client()
        checks = [
            ("get", "/api/stash/documents"),
            ("get", "/api/stash/documents/x"),
            ("get", "/api/stash/documents/x/cards"),
            ("get", "/api/stash/documents/x/toc"),
            ("get", "/api/stash/documents/x/quiz"),
            ("get", "/api/stash/saved"),
            ("get", "/api/stash/usage"),
            ("get", "/api/stash/cards/x"),
            ("post", "/api/stash/documents/x/regenerate"),
            ("post", "/api/stash/documents/x/progress"),
            ("post", "/api/stash/cards/x/save"),
            ("put", "/api/stash/cards/x/note"),
            ("delete", "/api/stash/cards/x/note"),
        ]
        for method, url in checks:
            self.assertEqual(getattr(c, method)(url).status_code, 401, url)

    def test_upload_then_read_cycle_via_api(self):
        c = self.client(self.UID_A)
        self.patch_provider()
        blob = build_pdf(PAGES_2_SECTIONS)
        r = c.post(
            "/api/stash/documents",
            data={"file": (io.BytesIO(blob), "sample.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(r.status_code, 201, r.data)
        doc = r.get_json()["document"]
        self.assertEqual(doc["status"], "queued")
        row = db._query_one("SELECT id FROM stash_jobs WHERE document_id = ?", (doc["id"],))
        repository.update_job(row["id"], status="done")
        service.process_document(doc["id"])

        cards_r = c.get("/api/stash/documents/%s/cards" % doc["id"])
        self.assertEqual(cards_r.status_code, 200)
        payload = cards_r.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total"], 6)
        card = payload["cards"][1]
        self.assertIn("saved", card)
        self.assertIn("status", card)

        toc_r = c.get("/api/stash/documents/%s/toc" % doc["id"])
        self.assertEqual(toc_r.status_code, 200)
        self.assertEqual(len(toc_r.get_json()["sections"]), 2)

        quiz_r = c.get("/api/stash/documents/%s/quiz" % doc["id"])
        self.assertTrue(quiz_r.get_json()["ok"])
        self.assertGreaterEqual(len(quiz_r.get_json()["quiz"]["questions"]), 2)

        detail_r = c.get("/api/stash/cards/%s" % card["id"])
        detail = detail_r.get_json()["card"]
        self.assertIn("ask_prompt", detail)
        self.assertIn("#ask=", detail["ask_url"])
        self.assertEqual(detail["saved"], 0)
        self.assertEqual(detail["note"], None)

    def test_duplicate_upload_returns_409(self):
        c = self.client(self.UID_A)
        blob = build_pdf(PAGES_2_SECTIONS)
        first = c.post(
            "/api/stash/documents",
            data={"file": (io.BytesIO(blob), "sample.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(first.status_code, 201)
        second = c.post(
            "/api/stash/documents",
            data={"file": (io.BytesIO(blob), "copy.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(second.status_code, 409)
        body = second.get_json()
        self.assertTrue(body["duplicate"])

    def test_card_state_notes_highlights_and_progress(self):
        c = self.client(self.UID_A)
        self.patch_provider()
        doc, _ = self.make_doc()
        service.process_document(doc["id"])
        cards = repository.list_cards(doc["id"], limit=50)
        card = next(cd for cd in cards if cd["card_type"] == "key_takeaway")

        for url, expect_saved in (("/save", 1), ("/unsave", 0)):
            self.assertEqual(c.post("/api/stash/cards/%s%s" % (card["id"], url)).status_code, 200)
            self.assertEqual(repository.get_card_state(self.UID_A, card["id"])["is_saved"], expect_saved)

        self.assertEqual(c.post("/api/stash/cards/%s/gotit" % card["id"]).status_code, 200)
        self.assertEqual(repository.get_card_state(self.UID_A, card["id"])["status"], "got_it")
        self.assertEqual(c.post("/api/stash/cards/%s/gotit" % card["id"]).status_code, 200)
        self.assertEqual(repository.get_card_state(self.UID_A, card["id"])["status"], "")

        self.assertEqual(c.post("/api/stash/cards/%s/review" % card["id"]).status_code, 200)
        self.assertEqual(repository.get_card_state(self.UID_A, card["id"])["status"], "review")

        self.assertEqual(
            c.put("/api/stash/cards/%s/note" % card["id"],
                  json={"content": "remember to compare with meiosis"}).status_code, 200)
        self.assertEqual(repository.get_note(self.UID_A, card["id"])["content"],
                         "remember to compare with meiosis")
        self.assertEqual(c.delete("/api/stash/cards/%s/note" % card["id"]).status_code, 200)
        self.assertIsNone(repository.get_note(self.UID_A, card["id"]))

        r = c.post(
            "/api/stash/cards/%s/highlight" % card["id"],
            json={"field": "body", "start_offset": 2, "end_offset": 11, "color": "green"},
        )
        self.assertEqual(r.status_code, 200)
        hid = r.get_json()["highlight_id"]
        self.assertEqual(len(repository.list_highlights(self.UID_A, card["id"])), 1)
        self.assertEqual(c.delete("/api/stash/highlights/%s" % hid).status_code, 200)
        self.assertEqual(len(repository.list_highlights(self.UID_A, card["id"])), 0)

        self.assertEqual(c.post(
            "/api/stash/documents/%s/progress" % doc["id"],
            json={"last_position": 3, "cards_seen": True, "cards_got_it": True}).status_code, 200)
        prog = repository.get_progress(self.UID_A, doc["id"])
        self.assertEqual(prog["last_card_position"], 3)
        self.assertEqual(prog["cards_seen"], 1)
        self.assertEqual(prog["cards_got_it"], 1)

    def test_upload_and_regenerate_register_provider_pick(self):
        c = self.client(self.UID_A)
        self.patch_provider()
        client.usable_providers = lambda uid: [_ANTHROPIC_OPTION]
        blob = build_pdf(PAGES_2_SECTIONS)

        r = c.post(
            "/api/stash/documents",
            data={
                "file": (io.BytesIO(blob), "sample.pdf"),
                "provider": "anthropic",
                "model": "claude",
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(r.status_code, 201, r.data)
        doc = r.get_json()["document"]
        self.assertEqual(doc["preferred_provider"], "anthropic")
        self.assertEqual(doc["preferred_model"], "claude")

        row = db._query_one("SELECT id FROM stash_jobs WHERE document_id = ?", (doc["id"],))
        repository.update_job(row["id"], status="done")
        regen = c.post(
            "/api/stash/documents/%s/regenerate" % doc["id"],
            json={"provider": "anthropic", "model": "claude"},
        )
        self.assertEqual(regen.status_code, 200, regen.data)
        self.assertEqual(regen.get_json()["document"]["preferred_provider"], "anthropic")

    def test_upload_rejects_unknown_provider_pick(self):
        c = self.client(self.UID_A)
        client.usable_providers = lambda uid: []
        blob = build_pdf(PAGES_2_SECTIONS)
        r = c.post(
            "/api/stash/documents",
            data={
                "file": (io.BytesIO(blob), "sample.pdf"),
                "provider": "anthropic",
                "model": "claude",
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("isn't available", r.get_json()["error"])

    def test_search_and_delete_documents(self):
        c = self.client(self.UID_A)
        doc, _ = self.make_doc()
        listed = c.get("/api/stash/documents?q=sample").get_json()["documents"]
        self.assertEqual(len(listed), 1)
        empty = c.get("/api/stash/documents?q=zzzznomatch").get_json()["documents"]
        self.assertEqual(len(empty), 0)
        self.assertEqual(c.delete("/api/stash/documents/%s" % doc["id"]).status_code, 200)
        self.assertEqual(c.get("/api/stash/documents/%s" % doc["id"]).status_code, 404)

    def test_cross_user_authorization_is_enforced(self):
        c = self.client(self.UID_B)
        doc, blob = self.make_doc(uid=self.UID_A)
        self.patch_provider()
        service.process_document(doc["id"])
        card = repository.list_cards(doc["id"], limit=50)[0]
        for method, url in (
            ("get", "/api/stash/documents/%s" % doc["id"]),
            ("get", "/api/stash/documents/%s/cards" % doc["id"]),
            ("get", "/api/stash/documents/%s/toc" % doc["id"]),
            ("get", "/api/stash/documents/%s/quiz" % doc["id"]),
            ("delete", "/api/stash/documents/%s" % doc["id"]),
            ("post", "/api/stash/documents/%s/regenerate" % doc["id"]),
            ("post", "/api/stash/cards/%s/save" % card["id"]),
            ("put", "/api/stash/cards/%s/note" % card["id"]),
            ("get", "/api/stash/cards/%s" % card["id"]),
        ):
            self.assertEqual(getattr(c, method)(url).status_code, 404, (method, url))
        self.assertEqual(c.get("/stash/reader/%s" % doc["id"]).status_code, 404)
        saved = c.get("/api/stash/saved").get_json()["cards"]
        self.assertEqual(saved, [])


class StashUploadTest(StashTestBase):
    def test_classify_rejects_bad_extensions_and_forged_content(self):
        with self.assertRaises(security.StashSecurityError):
            security.classify("notes.txt", b"%PDF-1.4 junk")
        with self.assertRaises(security.StashSecurityError):
            security.classify("sample.pdf", b"this is not a pdf")
        ext, kind = security.classify("sample.pdf", build_pdf(["Mitosis divides cells."]))
        self.assertEqual((ext, kind), ("pdf", "pdf"))

    def test_read_upload_enforces_byte_cap(self):
        blob = build_pdf(PAGES_2_SECTIONS)
        with self.assertRaises(security.StashSecurityError):
            security.read_upload(io.BytesIO(blob), max_bytes=2000)

    def test_upload_api_rejects_non_pdf(self):
        c = self.client(self.UID_A)
        r = c.post(
            "/api/stash/documents",
            data={"file": (io.BytesIO(b"plain text"), "notes.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("Unsupported file type", r.get_json()["error"])

    def test_upload_page_explains_when_no_key_is_configured(self):
        c = self.client(self.UID_A)
        html = c.get("/stash/upload").get_data(as_text=True)
        self.assertIn("Stash needs an AI key", html)
        self.assertIn('data-max-mb="50"', html)


if __name__ == "__main__":
    unittest.main()