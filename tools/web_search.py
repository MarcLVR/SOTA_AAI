"""
Web search tool using DuckDuckGo (no API key required).
Wraps ddg results into a LangChain @tool.
"""
from langchain_core.tools import tool
from ddgs import DDGS
from loguru import logger


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """
    Search the web using DuckDuckGo and return a summary of the top results.
    Use this to find current information, news, documentation, or facts.

    Args:
        query: The search query string.
        max_results: Number of results to return (default 5).
    """
    logger.info(f"[web_search] query='{query}' max_results={max_results}")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return "No results found."

        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append(
                f"[{i}] **{r.get('title', 'No title')}**\n"
                f"URL: {r.get('href', '')}\n"
                f"{r.get('body', '')}"
            )
        return "\n\n".join(formatted)

    except Exception as e:
        logger.error(f"[web_search] error: {e}")
        return f"Search failed: {str(e)}"
