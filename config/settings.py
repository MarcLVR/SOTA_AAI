"""
Centralised config via pydantic-settings.
All values can be overridden with environment variables or a .env file.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Ollama ────────────────────────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_embed_model: str = "nomic-embed-text"

    # ── LLM provider selection ────────────────────────────────────────────────
    llm_provider: str = "gemini"                       # default: gemini | groq | ollama
    gemini_model: str = "gemini-2.5-flash"
    google_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_api_key: str | None = None

    # ── Optional remote fallback ───────────────────────────────────────────────
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # ── Agent behaviour ────────────────────────────────────────────────────────
    max_iterations: int = 10
    max_supervisor_rounds: int = 5
    temperature: float = 0.0

    # ── Reflection ────────────────────────────────────────────────────────────
    critic_revision_threshold: float = 0.70   # below this score → revise
    critic_max_revisions: int = 2

    # ── RAG ───────────────────────────────────────────────────────────────────
    chroma_persist_dir: str = "./data/chroma_db"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    top_k_retrieval: int = 5

    # ── MCP ───────────────────────────────────────────────────────────────────
    mcp_filesystem_root: str = "./data/uploads"

    # ── Observability ─────────────────────────────────────────────────────────
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    langchain_api_key: str | None = None          # LangSmith

    # ── Guardrails ────────────────────────────────────────────────────────────
    guardrail_max_input_length: int = 8000
    guardrail_redact_pii: bool = True

    @property
    def chroma_path(self) -> Path:
        return Path(self.chroma_persist_dir)

    @property
    def uploads_path(self) -> Path:
        return Path(self.mcp_filesystem_root)


settings = Settings()
