"""
OmniMCP client — wraps the MCP server at OMNI_MCP_URL.

Implements the MCP Streamable HTTP transport (2025 spec):
  1. POST /  {"jsonrpc":"2.0","method":"initialize",...}  → get session ID from
     the Mcp-Session-Id response header
  2. POST /  {"jsonrpc":"2.0","method":"tools/list",...}  with that header
  3. POST /  {"jsonrpc":"2.0","method":"tools/call",...}  with that header
"""

import json
import logging
from functools import lru_cache

import httpx
from langchain_core.tools import tool

from agent.config import settings


def _parse_sse_or_json(resp: httpx.Response) -> dict:
    """
    MCP Streamable HTTP servers may return either:
      - plain JSON body  (Content-Type: application/json)
      - SSE stream       (Content-Type: text/event-stream)
        where each message is a line like:  data: {"jsonrpc":...}
    Returns the parsed dict of the first JSON-RPC message found.
    """
    ct = resp.headers.get("content-type", "")
    text = resp.text.strip()
    if not text:
        raise ValueError("Empty response body")

    if "text/event-stream" in ct or text.startswith("data:"):
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    return json.loads(payload)
        raise ValueError(f"No data lines found in SSE response: {text[:200]!r}")

    return json.loads(text)


log = logging.getLogger("sisyphus.tools.mcp")

_BASE = settings.omni_mcp_url.rstrip("/")


# ── Session initialisation ─────────────────────────────────────────────────────


def _get_session_id(client: httpx.Client) -> str | None:
    """
    Perform the MCP initialize handshake and return the session ID,
    or None if the server doesn't require / return one.
    """
    try:
        resp = client.post(
            _BASE,
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "sisyphus", "version": "1.0"},
                },
                "id": 0,
            },
            headers={"Content-Type": "application/json"},
        )
        session_id = resp.headers.get("Mcp-Session-Id") or resp.headers.get(
            "mcp-session-id"
        )
        if session_id:
            log.info(f"OmniMCP: session initialised ({session_id[:16]}…)")
        return session_id
    except Exception as e:
        log.debug(f"OmniMCP initialize error: {e}")
        return None


# ── Tool discovery ─────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def discover_tools() -> list[dict]:
    """Return the list of tools advertised by OmniMCP (cached after first call)."""
    with httpx.Client(timeout=10) as client:

        # MCP Streamable HTTP: initialize → tools/list with session header
        session_id = _get_session_id(client)
        headers = {"Content-Type": "application/json"}
        if session_id:
            headers["Mcp-Session-Id"] = session_id

        try:
            resp = client.post(
                _BASE,
                json={"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1},
                headers=headers,
            )
            if resp.status_code == 200:
                data = _parse_sse_or_json(resp)
                tools = data.get("result", {}).get("tools", [])
                if tools:
                    log.info(f"OmniMCP: {len(tools)} tools discovered")
                    return tools
                log.warning(f"OmniMCP tools/list unexpected shape: {data}")
        except Exception as e:
            log.warning(f"OmniMCP tool discovery failed: {e}")

    return []


def tools_summary() -> str:
    """Return a compact text summary of available MCP tools for the system prompt."""
    tools = discover_tools()
    if not tools:
        return "OmniMCP: no tools available or server unreachable."
    lines = ["OmniMCP available tools:"]
    for t in tools:
        desc = t.get("description", "")
        lines.append(f"  - {t['name']}: {desc}")
    return "\n".join(lines)


# ── LangChain tool ─────────────────────────────────────────────────────────────


@tool
def call_mcp_tool(tool_name: str, arguments: str) -> str:
    """Call a tool on the OmniMCP server (http://192.168.2.117:9090).
    `tool_name` is the exact tool name (e.g. 'GetCalendarEvents').
    `arguments` is a JSON string of the arguments dict, e.g. '{"date": "2026-03-21"}'.
    Use discover_tools to know what tools are available."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        return f"arguments must be valid JSON. Got: {arguments!r}"

    with httpx.Client(timeout=30) as client:
        session_id = _get_session_id(client)
        headers = {"Content-Type": "application/json"}
        if session_id:
            headers["Mcp-Session-Id"] = session_id

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": args},
            "id": 1,
        }
        try:
            resp = client.post(_BASE, json=payload, headers=headers)
            if resp.status_code == 200:
                data = _parse_sse_or_json(resp)
                if "result" in data:
                    return json.dumps(data["result"], ensure_ascii=False)
                if "error" in data:
                    return f"MCP error: {data['error']}"
            return f"MCP tool call failed: HTTP {resp.status_code} — {resp.text[:200]}"
        except Exception as e:
            log.warning(f"call_mcp_tool({tool_name}) failed: {e}")
            return f"MCP tool call failed: {e}"
