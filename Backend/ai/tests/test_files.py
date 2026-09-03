"""Tests for file extraction and context truncation."""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)

from ai import files  # noqa: E402


class FileExtractionTest(unittest.TestCase):
    def test_text_file(self):
        res = files.extract_text("notes.txt", b"hello world")
        self.assertEqual(res["kind"], "text")
        self.assertEqual(res["text"], "hello world")

    def test_markdown_file(self):
        res = files.extract_text("readme.md", b"# Title\nbody")
        self.assertEqual(res["kind"], "text")
        self.assertIn("# Title", res["text"])

    def test_unsupported_extension(self):
        res = files.extract_text("archive.zip", b"PK\x03\x04")
        self.assertEqual(res["kind"], "unsupported")
        self.assertEqual(res["text"], "")

    def test_pdf_without_pypdf2(self):
        had = files._has_module("PyPDF2")
        res = files.extract_text("doc.pdf", b"%PDF-1.4 fake")
        if had:
            # library present -> reads (may error gracefully, still 'pdf' kind)
            self.assertEqual(res["kind"], "pdf")
        else:
            self.assertEqual(res["kind"], "pdf")
            self.assertIn("PyPDF2", res["note"])

    def test_image_extension(self):
        res = files.extract_text("photo.png", b"fakeimage")
        self.assertEqual(res["kind"], "image")
        # note always present regardless of Pillow
        self.assertTrue(res["note"])


class TruncationTest(unittest.TestCase):
    def test_short_text_unchanged(self):
        self.assertEqual(files.truncate_for_context("short", 1000), "short")

    def test_long_text_truncated(self):
        text = "x" * 500
        out = files.truncate_for_context(text, 100)
        self.assertEqual(len(out), 100 + len("\n...[truncated]"))
        self.assertTrue(out.endswith("...[truncated]"))

    def test_none_returns_empty(self):
        self.assertEqual(files.truncate_for_context(None), "")


if __name__ == "__main__":
    unittest.main()