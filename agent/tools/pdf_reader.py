import logging
from langchain_core.tools import tool

log = logging.getLogger("sisyphus.tools.pdf_reader")

# Cap extracted text to avoid blowing the context window
_MAX_CHARS = 20_000


@tool
def read_pdf(url: str) -> str:
    """Fetch a PDF document from a URL and return its extracted text content.
    Use this when a web search result links to a PDF paper, report, or document."""
    try:
        import httpx
        import pymupdf  # PyMuPDF

        response = httpx.get(url, follow_redirects=True, timeout=30)
        response.raise_for_status()

        if "pdf" not in response.headers.get(
            "content-type", ""
        ).lower() and not url.lower().endswith(".pdf"):
            return f"URL does not appear to be a PDF (content-type: {response.headers.get('content-type', 'unknown')})."

        doc = pymupdf.open(stream=response.content, filetype="pdf")
        pages_text = []
        for page in doc:
            pages_text.append(page.get_text())
        doc.close()

        text = "\n".join(pages_text).strip()
        if not text:
            return "PDF was fetched but contained no extractable text (may be image-based)."

        if len(text) > _MAX_CHARS:
            text = (
                text[:_MAX_CHARS]
                + f"\n\n[... truncated after {_MAX_CHARS} characters ...]"
            )

        return text
    except Exception as e:
        log.warning(f"read_pdf failed for {url}: {e}")
        return f"Failed to read PDF from {url}: {e}"
