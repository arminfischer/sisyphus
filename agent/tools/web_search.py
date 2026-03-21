import logging
from langchain_core.tools import tool

log = logging.getLogger("sisyphus.tools.web_search")


@tool
def web_search(query: str) -> str:
    """Search the web using DuckDuckGo and return a summary of the top results.
    Use this for current events, news, and general research questions."""
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=6))

        if not results:
            return "No results found for this query."

        lines = []
        for r in results:
            lines.append(f"**{r['title']}**\nURL: {r['href']}\n{r['body']}")
        return "\n\n---\n\n".join(lines)
    except Exception as e:
        log.warning(f"web_search failed: {e}")
        return f"Search failed: {e}"
