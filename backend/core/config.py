"""
Ethereal Engine — Core Configuration
All settings loaded from environment variables with sensible defaults.
"""
import os
from functools import lru_cache
from pathlib import Path
import shutil

from pydantic_settings import BaseSettings


BASE_DIR = Path(__file__).resolve().parents[1]

LOCAL_DEV_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3010",
    "http://127.0.0.1:3010",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]
LOCAL_DEV_CORS_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
WINDOWS_TESSERACT_CANDIDATES = [
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
]


class Settings(BaseSettings):
    # ── App ───────────────────────────────────────────────────────────────────
    app_name: str = "Ethereal Engine"
    app_version: str = "2.4.0"
    debug: bool = False

    # ── CORS ─────────────────────────────────────────────────────────────────
    cors_origins: str = ",".join(LOCAL_DEV_CORS_ORIGINS)

    @property
    def cors_origins_list(self) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()

        for origin in [*LOCAL_DEV_CORS_ORIGINS, *self.cors_origins.split(",")]:
            normalized = origin.strip()
            if normalized and normalized not in seen:
                merged.append(normalized)
                seen.add(normalized)

        return merged

    @property
    def cors_origin_regex(self) -> str:
        return LOCAL_DEV_CORS_REGEX

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return (BASE_DIR / path).resolve()

    @property
    def chroma_path_resolved(self) -> Path:
        return self.resolve_path(self.chroma_path)

    @property
    def upload_path_resolved(self) -> Path:
        return self.resolve_path(self.upload_path)

    @property
    def tesseract_cmd_resolved(self) -> str:
        configured = self.tesseract_cmd.strip()
        if configured:
            path = Path(configured)
            return str(path if path.is_absolute() else self.resolve_path(configured))

        discovered = shutil.which("tesseract")
        if discovered:
            return discovered

        for candidate in WINDOWS_TESSERACT_CANDIDATES:
            if candidate.exists():
                return str(candidate)

        local_appdata = (
            Path.home()
            / "AppData"
            / "Local"
            / "Programs"
            / "Tesseract-OCR"
            / "tesseract.exe"
        )
        if local_appdata.exists():
            return str(local_appdata)

        return ""

    # ── Ollama ────────────────────────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_llm_model: str = "llama3.2"          # LLM for generation
    ollama_embed_model: str = "nomic-embed-text" # Embeddings model
    ollama_timeout: int = 120
    generation_provider: str = "auto"
    embedding_provider: str = "auto"
    query_understanding_provider: str = "auto"
    local_embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    preload_embedding_model: bool = False
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "llama-3.1-8b-instant"
    groq_timeout: int = 60

    @property
    def groq_enabled(self) -> bool:
        return bool(self.groq_api_key.strip())

    @property
    def preferred_generation_provider(self) -> str:
        preferred = self.generation_provider.strip().lower()
        if preferred == "groq":
            return "groq" if self.groq_enabled else "ollama"
        if preferred == "ollama":
            return "ollama"
        return "groq" if self.groq_enabled else "ollama"

    @property
    def is_cloud_deploy(self) -> bool:
        return bool(os.getenv("RENDER") or os.getenv("VERCEL"))

    @property
    def preferred_embedding_provider(self) -> str:
        preferred = self.embedding_provider.strip().lower()
        if preferred in {"ollama", "local"}:
            return preferred
        return "local" if self.is_cloud_deploy else "ollama"

    @property
    def preferred_query_understanding_provider(self) -> str:
        preferred = self.query_understanding_provider.strip().lower()
        if preferred in {"rule", "ollama"}:
            return preferred
        return "rule" if self.is_cloud_deploy else "ollama"

    @property
    def uses_ollama_generation(self) -> bool:
        return self.preferred_generation_provider == "ollama"

    @property
    def uses_ollama_embeddings(self) -> bool:
        return self.preferred_embedding_provider == "ollama"

    @property
    def uses_ollama_query_understanding(self) -> bool:
        return self.preferred_query_understanding_provider == "ollama"

    # ── ChromaDB ─────────────────────────────────────────────────────────────
    chroma_path: str = "./chroma_db"
    chroma_collection: str = "ethereal_docs"

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"
    cache_ttl: int = 3600  # 1 hour

    # ── Ingestion ─────────────────────────────────────────────────────────────
    upload_path: str = "./uploads"
    max_file_size_mb: int = 50
    max_crawl_depth: int = 2
    max_crawl_pages: int = 20
    tesseract_cmd: str = ""

    # ── Chunking ─────────────────────────────────────────────────────────────
    chunk_size: int = 512
    chunk_overlap: int = 64
    semantic_chunk_threshold: float = 0.85

    # ── Retrieval ────────────────────────────────────────────────────────────
    retrieval_top_k: int = 10         # Initial retrieval count
    rerank_top_k: int = 5             # After reranking
    bm25_weight: float = 0.3          # Hybrid search BM25 weight
    vector_weight: float = 0.7        # Hybrid search vector weight

    # ── Generation ───────────────────────────────────────────────────────────
    max_context_tokens: int = 4096
    generation_temperature: float = 0.1
    generation_max_tokens: int = 1024

    class Config:
        env_file = str(BASE_DIR / ".env")
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
