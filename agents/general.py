"""
General agent — handles everything else: reasoning, planning, Q&A, synthesis.
Has access to all tools.
"""
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from .llm import get_llm
from .middleware import build_summarizer
from tools import GENERAL_TOOLS
from memory import retrieve_from_memory

GENERAL_SYSTEM = SystemMessage(content="""You are a knowledgeable, helpful AI assistant.
You have access to web search, code execution, file tools, and a personal knowledge base.
Think step by step. Use tools when you need current information or need to compute something.
Synthesise information clearly and concisely. When referencing documents from memory,
quote the relevant passage and name the source.""")


def build_general_agent(extra_tools=None):
    tools = GENERAL_TOOLS + [retrieve_from_memory] + (extra_tools or [])
    return create_agent(
        model=get_llm(),
        tools=tools,
        system_prompt=GENERAL_SYSTEM.content,
        middleware=[build_summarizer()],
    )
