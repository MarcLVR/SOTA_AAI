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
    # Any provider supported by LangChain's init_chat_model works here with zero
    # code changes — just set LLM_PROVIDER + the matching model/key. The values
    # below give first-class defaults for the providers we ship and test.
    llm_provider: str = "anthropic"   # anthropic | groq | ollama | openai | mistralai | google_genai | bedrock | …
    llm_model: str | None = None      # generic model override for providers without a dedicated field below

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-haiku-4-5-20251001"
    groq_model: str = "llama-3.3-70b-versatile"
    groq_api_key: str | None = None

    # OpenAI / OpenAI-compatible endpoints (vLLM, LM Studio, OpenRouter, Together, …)
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str | None = None   # set to point at any OpenAI-compatible server

    # ── Agent behaviour ────────────────────────────────────────────────────────
    max_iterations: int = 10
    max_supervisor_rounds: int = 5
    temperature: float = 0.0

    # ── LLM call resilience ────────────────────────────────────────────────────
    llm_max_retries: int = 2          # extra attempts on transient errors (total = 1 + this)
    llm_retry_max_wait: float = 8.0   # cap on exponential backoff between retries (seconds)

    # ── Context compaction (long-thread protection) ────────────────────────────
    context_max_messages: int = 24    # compact when a thread's history exceeds this many messages
    context_keep_last: int = 10       # most-recent messages always kept verbatim
    context_summarize: bool = False   # True → summarise dropped messages with one LLM call

    # ── Plan-and-execute (opt-in) ──────────────────────────────────────────────
    planner_enabled: bool = False     # set PLANNER_ENABLED=true to route via the planner subgraph
    planner_max_replans: int = 2      # max replanning rounds per request on step failure

    # ── Reflection ────────────────────────────────────────────────────────────
    critic_enabled: bool = False               # set CRITIC_ENABLED=true to enable Reflexion
    critic_revision_threshold: float = 0.70   # below this score → revise
    critic_max_revisions: int = 2

    # ── RAG ───────────────────────────────────────────────────────────────────
    chroma_persist_dir: str = "./data/chroma_db"
    # bge-small-en-v1.5: 384-dim, CPU-only, no service dependency, stronger than
    # all-MiniLM-L6-v2 (MTEB ~62 vs ~56). Kept local so the Anthropic path needs no Ollama.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    top_k_retrieval: int = 5
    rag_grade: bool = True            # CRAG-style LLM relevance grading of retrieved chunks
    rag_query_rewrite: bool = True    # rewrite the query + re-retrieve once when grading says "insufficient"
    rag_rerank: bool = True           # second-stage cross-encoder reranking of retrieved chunks
    rag_rerank_model: str = "BAAI/bge-reranker-base"   # CPU-friendly; override to bge-reranker-v2-m3 for max quality
    rag_fetch_k: int = 20             # candidates fetched before rerank/grade narrow them down

    # ── Tool-result caching ────────────────────────────────────────────────────
    tool_cache_ttl: int = 300         # seconds to cache idempotent tool results (web_search); 0 disables

    # ── MCP ───────────────────────────────────────────────────────────────────
    mcp_filesystem_root: str = "./data/uploads"
    # JSON list of remote HTTP MCP servers, e.g.:
    # [{"name":"my-server","transport":"streamable_http","url":"https://...","headers":{"Authorization":"Bearer sk-..."}}]
    # Supported transports: "sse" | "streamable_http"
    mcp_http_servers: str = "[]"

    # ── Observability ─────────────────────────────────────────────────────────
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    langchain_api_key: str | None = None          # LangSmith

    # ── Semantic compliance layer ─────────────────────────────────────────────
    semantic_enabled: bool = False   # set True to enable ChromaDB standards comparison

    # ── Analytics / PostgreSQL ────────────────────────────────────────────────
    db_enabled: bool = False   # set True only when Postgres is reachable; protects demo

    # ── Application identity ──────────────────────────────────────────────────
    app_name: str = "AI Auditor"          # displayed in UI, bot messages, executor prompts
    brand_name: str = ""                   # canonical brand name to check; "" disables brand check
    default_owner: str = ""               # fallback owner when doc author cannot be resolved
    owner_usernames: str = ""             # comma-separated owner usernames, e.g. "alice,bob"

    # ── Guardrails ────────────────────────────────────────────────────────────
    guardrail_max_input_length: int = 8000
    guardrail_redact_pii: bool = True

    # ── Permissions ───────────────────────────────────────────────────────────
    active_role: str = "admin"          # default role: viewer | analyst | admin
    permission_keys: str = "{}"         # JSON dict mapping api_key → role

    @property
    def chroma_path(self) -> Path:
        return Path(self.chroma_persist_dir)

    @property
    def uploads_path(self) -> Path:
        return Path(self.mcp_filesystem_root)


settings = Settings()
