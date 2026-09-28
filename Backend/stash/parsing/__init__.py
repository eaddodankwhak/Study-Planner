"""Document parsing for Stash: PDF + PPTX -> page list -> sections/chunks."""

from . import chunker, cleaner, pdf_parser, pptx_parser

__all__ = ["chunker", "cleaner", "pdf_parser", "pptx_parser", "parse_document"]


def parse_document(data, ext):
    """Parse uploaded bytes into the normalized Stash page structure."""
    if ext == "pdf":
        return pdf_parser.parse_pdf(data)
    if ext == "pptx":
        return pptx_parser.parse_pptx(data)
    raise ValueError(f"unsupported stash source type: {ext}")


def build_structure(parsed, chunk_target=None, chunk_max=None):
    """Full structure pipeline from a parse*() result.

    chunk_target/chunk_max size the generated chunks; defaults come from the
    chunker's constants when omitted.
    """
    from .cleaner import identify_boilerplate, strip_boilerplate

    pages = strip_boilerplate(parsed["pages"], identify_boilerplate(parsed["pages"]))
    return pages, chunker.build_structure(
        pages,
        parsed.get("outline"),
        parsed["kind"],
        chunk_target=chunk_target if chunk_target is not None else chunker.CHUNK_TARGET,
        chunk_max=chunk_max if chunk_max is not None else chunker.CHUNK_MAX,
    )