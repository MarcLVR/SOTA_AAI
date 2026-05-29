from .web_search import web_search
from .code_executor import python_repl
from .file_tools import read_file, write_file, list_files

# Tool sets per agent role
RESEARCHER_TOOLS = [web_search, read_file, list_files]
CODER_TOOLS = [python_repl, read_file, write_file, list_files]
GENERAL_TOOLS = [web_search, python_repl, read_file, write_file, list_files]

__all__ = [
    "web_search",
    "python_repl",
    "read_file",
    "write_file",
    "list_files",
    "RESEARCHER_TOOLS",
    "CODER_TOOLS",
    "GENERAL_TOOLS",
]
