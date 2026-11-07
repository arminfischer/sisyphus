import ipaddress
import logging
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpcore
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


def _normalize_host(host: str | bytes) -> str:
    if isinstance(host, bytes):
        host = host.decode("ascii")
    host = host.strip().strip("[]").rstrip(".").lower()
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        return host.encode("idna").decode("ascii")


class _PinnedNetworkBackend:
    def __init__(self) -> None:
        self._backend = httpcore.SyncBackend()
        self._addresses: dict[tuple[str, int], str] = {}

    def pin(self, host: str, port: int, address: str) -> None:
        self._addresses[(_normalize_host(host), port)] = address

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        address = self._addresses.get((_normalize_host(host), port))
        if address is None:
            raise httpcore.ConnectError("Destination was not validated.")
        return self._backend.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    def connect_unix_socket(self, *args, **kwargs):
        return self._backend.connect_unix_socket(*args, **kwargs)

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


class _HttpcoreResponseStream(httpx.SyncByteStream):
    def __init__(self, stream) -> None:
        self._stream = stream

    def __iter__(self):
        try:
            yield from self._stream
        finally:
            self._stream.close()

    def close(self) -> None:
        self._stream.close()


class _PinnedTransport(httpx.BaseTransport):
    def __init__(self, backend: _PinnedNetworkBackend) -> None:
        self._pool = httpcore.ConnectionPool(
            network_backend=backend,
            max_connections=4,
            max_keepalive_connections=2,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            response = self._pool.handle_request(core_request)
        except httpcore.TimeoutException as exc:
            raise httpx.TimeoutException(str(exc), request=request) from exc
        except httpcore.ConnectError as exc:
            raise httpx.ConnectError(str(exc), request=request) from exc
        except httpcore.NetworkError as exc:
            raise httpx.NetworkError(str(exc), request=request) from exc
        except httpcore.ProtocolError as exc:
            raise httpx.ProtocolError(str(exc), request=request) from exc

        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_HttpcoreResponseStream(response.stream),
            extensions=response.extensions,
        )

    def close(self) -> None:
        self._pool.close()


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


def _allowed_url(url: str, backend: _PinnedNetworkBackend) -> tuple[bool, str]:
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
    host = _normalize_host(parsed.hostname)
    if ":" in host:
        host = f"[{host}]"
    authority = f"{host}:{effective_port}"
    allowed = {
        item.strip().lower().rstrip("/")
        for item in settings.web_fetch_allowed_hosts.split(",")
        if item.strip()
    }
    explicitly_allowed = authority in allowed
    try:
        try:
            addresses = [ipaddress.ip_address(host.strip("[]"))]
        except ValueError:
            addresses = list(
                dict.fromkeys(
                    ipaddress.ip_address(result[4][0].split("%", 1)[0])
                    for result in socket.getaddrinfo(
                        host,
                        effective_port,
                        type=socket.SOCK_STREAM,
                    )
                )
            )
    except (OSError, ValueError):
        return (
            False,
            f"Host {authority} could not be resolved to a valid IP address.",
        )

    if not addresses:
        return False, f"Host {authority} did not resolve to an IP address."
    if not explicitly_allowed and any(not address.is_global for address in addresses):
        return (
            False,
            f"Host {authority} resolves to a non-public IP address. For a trusted "
            "private service, add its exact host:port to WEB_FETCH_ALLOWED_HOSTS.",
        )

    backend.pin(parsed.hostname, effective_port, addresses[0].compressed)
    return True, ""


@tool
def read_webpage(url: str) -> str:
    """Fetch a text, HTML, JSON, or XML page from a public host or trusted local host.

    Publicly routable hosts are available by default. Local/private hosts require
    an exact host:port entry in WEB_FETCH_ALLOWED_HOSTS. Each DNS result is checked
    and pinned for the request; redirects are validated independently.
    """
    current_url = url
    backend = _PinnedNetworkBackend()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,
            transport=_PinnedTransport(backend),
        ) as client:
            for redirect_count in range(_MAX_REDIRECTS + 1):
                allowed, reason = _allowed_url(current_url, backend)
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
