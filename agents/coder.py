"""
Coder agent — specialises in writing, executing, and explaining code.
Tools: python_repl, read_file, write_file, list_files.
"""
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import SystemMessage

from .llm import get_llm
from tools import CODER_TOOLS

CODER_SYSTEM = SystemMessage(content="""You are the Coder agent.
You write clean, well-commented Python code and execute it to verify correctness.
When asked for code:
  1. Write the code.
  2. Execute it with the python_repl tool.
  3. Report the output and explain what it does.
Always handle edge cases and mention any assumptions.
Prefer pandas/numpy/matplotlib for data tasks.""")


def build_coder_agent(extra_tools=None):
    tools = CODER_TOOLS + (extra_tools or [])
    return create_react_agent(
        model=get_llm(),
        tools=tools,
        prompt=CODER_SYSTEM,
    )
