"""
OmniMCP client — wraps the MCP server at OMNI_MCP_URL.

The client:
1. Discovers available tools on startup via discover_tools().
2. Exposes a single LangChain tool `call_mcp_tool` so the LLM can call any
   MCP tool by name with a JSON arguments dict.
3. Tries both the JSON-RPC 2.0 envelope (standard MCP HTTP transport) and a
   simple REST-style POST as fallback.
"""

import json
import logging
from functools import lru_cache

import httpx
from langchain_core.tools import tool

from agent.config import settings

log = logging.getLogger("sisyphus.tools.mcp")

_BASE = settings.omni_mcp_url.rstrip("/")


# ── Tool discovery ─────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def discover_tools() -> list[dict]:
    """Return the list of tools advertised by OmniMCP (cached after first call)."""
    try:
        # Try JSON-RPC 2.0 envelope (standard MCP HTTP transport)
        with httpx.Client(timeout=8) as client:
            resp = client.post(
                _BASE,
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                tools = data.get("result", {}).get("tools", [])
                if tools:
                    log.info(f"OmniMCP: discovered {len(tools)} tools via JSON-RPC")
                    return tools

        # Fallback: REST-style GET /tools
        with httpx.Client(timeout=8) as client:
            resp = client.get(f"{_BASE}/tools")
            if resp.status_code == 200:
                tools = resp.json()
                if isinstance(tools, list):
                    log.info(f"OmniMCP: discovered {len(tools)} tools via REST")
                    return tools
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

    # Try JSON-RPC 2.0 first
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
        "id": 1,
    }
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                _BASE,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if "result" in data:
                    return json.dumps(data["result"], ensure_ascii=False)
                if "error" in data:
                    return f"MCP error: {data['error']}"

        # Fallback: REST-style POST /tools/{name}
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{_BASE}/tools/{tool_name}",
                json={"arguments": args},
            )
            resp.raise_for_status()
            return json.dumps(resp.json(), ensure_ascii=False)
    except Exception as e:
        log.warning(f"call_mcp_tool({tool_name}) failed: {e}")
        return f"MCP tool call failed: {e}"
