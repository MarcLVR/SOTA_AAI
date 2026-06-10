"""
Researcher agent — specialises in information retrieval.
Tools: web_search, retrieve_from_memory, read_file, list_files.
"""
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from .llm import get_llm
from tools import RESEARCHER_TOOLS
from memory import retrieve_from_memory

RESEARCHER_SYSTEM = SystemMessage(content="""You are the Researcher agent.
Your job is to find accurate, up-to-date information using web search and the
knowledge base (vector memory). Always cite your sources. Be concise and factual.
If the knowledge base has relevant context, prefer it over web search to reduce
latency. Use web search for recent events or information not in memory.""")


def build_researcher_agent(extra_tools=None):
    tools = RESEARCHER_TOOLS + [retrieve_from_memory] + (extra_tools or [])
    return create_agent(
        model=get_llm(),
        tools=tools,
        system_prompt=RESEARCHER_SYSTEM.content,
    )
