import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from langchain_core.tools import tool

from agent.config import settings

log = logging.getLogger("sisyphus.tools.web_page")

_MAX_RESPONSE_BYTES = 1_000_000
_MAX_TEXT_CHARS = 30_000
_MAX_REDIRECTS = 3
_IGNORED_TAGS = {"script", "style", "noscript", "svg"}
_BLOCK_TAGS = {
    "address",
    "article",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "ol",
    "p",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
_ALLOWED_CONTENT_TYPES = {
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "text/csv",
    "text/html",
    "text/plain",
    "text/xml",
}


class _PageTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._ignored_depth:
            if tag in _IGNORED_TAGS:
                self._ignored_depth += 1
            return
        if tag in _IGNORED_TAGS:
            self._ignored_depth = 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_depth:
            if tag in _IGNORED_TAGS:
                self._ignored_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def _allowed_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False, "URL has an invalid port."

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, "Only absolute HTTP and HTTPS URLs can be fetched."
    if parsed.username or parsed.password:
        return False, "URLs containing credentials are not allowed."

    effective_port = port or (443 if parsed.scheme == "https" else 80)
    host = parsed.hostname.rstrip(".").lower()
    if ":" in host:
        host = f"[{host}]"
    authority = f"{host}:{effective_port}"
    allowed = {
        item.strip().lower().rstrip("/")
        for item in settings.web_fetch_allowed_hosts.split(",")
        if item.strip()
    }
    if authority not in allowed:
        return (
            False,
            f"Host {authority} is not allowlisted; add that exact host:port to "
            "WEB_FETCH_ALLOWED_HOSTS and restart the agent.",
        )
    return True, ""


@tool
def read_webpage(url: str) -> str:
    """Fetch a text, HTML, JSON, or XML page from an explicitly allowlisted host:port.

    Use for direct URLs supplied by the user, including local dashboards and APIs.
    The HTTP request runs from the agent host, not the model provider. Each redirect
    is checked against the same exact host:port allowlist.
    """
    current_url = url
    try:
        with httpx.Client(
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for redirect_count in range(_MAX_REDIRECTS + 1):
                allowed, reason = _allowed_url(current_url)
                if not allowed:
                    return f"Page access blocked. {reason}"

                with client.stream(
                    "GET",
                    current_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/json,"
                        "text/plain,application/xml,text/xml,text/csv"
                    },
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location or redirect_count == _MAX_REDIRECTS:
                            return "Page redirect limit reached or redirect target missing."
                        current_url = urljoin(current_url, location)
                        continue

                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    media_type = content_type.split(";", 1)[0].strip().lower()
                    if (
                        media_type not in _ALLOWED_CONTENT_TYPES
                        and not media_type.endswith(("+json", "+xml"))
                    ):
                        return (
                            "Unsupported page content type: "
                            f"{media_type or '(missing)'}. Expected HTML, JSON, XML, "
                            "CSV, or plain text."
                        )

                    body = bytearray()
                    truncated = False
                    for chunk in response.iter_bytes():
                        remaining = _MAX_RESPONSE_BYTES - len(body)
                        if len(chunk) > remaining:
                            body.extend(chunk[:remaining])
                            truncated = True
                            break
                        body.extend(chunk)
                    encoding = response.encoding or "utf-8"

                text = bytes(body).decode(encoding, errors="replace")
                if media_type in {"text/html", "application/xhtml+xml"}:
                    parser = _PageTextParser()
                    parser.feed(text)
                    text = parser.get_text()
                else:
                    text = text.strip()

                if not text:
                    return "The page returned no readable text."
                if len(text) > _MAX_TEXT_CHARS:
                    text = text[:_MAX_TEXT_CHARS]
                    truncated = True
                if truncated:
                    text += "\n\n[Page content truncated at the configured size limit.]"
                return f"Page content ({media_type}):\n\n{text}"

    except httpx.TimeoutException:
        return "Page request timed out."
    except httpx.HTTPStatusError as exc:
        return f"Page request failed with HTTP {exc.response.status_code}."
    except httpx.HTTPError as exc:
        log.warning("read_webpage failed: %s", type(exc).__name__)
        return f"Page request failed: {type(exc).__name__}."
    except Exception as exc:
        log.exception("Unexpected read_webpage failure")
        return f"Page retrieval failed: {type(exc).__name__}."

    return "Page redirect limit reached."
