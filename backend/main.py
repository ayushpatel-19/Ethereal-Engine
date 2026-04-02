"""
Ethereal Engine — FastAPI Application
Entry point. Mounts all routes, configures CORS, sets up lifecycle events.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from core.config import get_settings
from api.routes import router
from storage.store import chroma_store, bm25_index, graph_index, warm_embedding_backend

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup + shutdown lifecycle."""
    logger.info(f"🚀 Starting {settings.app_name} v{settings.app_version}")

    # Ensure upload directory exists
    settings.upload_path_resolved.mkdir(parents=True, exist_ok=True)
    settings.chroma_path_resolved.mkdir(parents=True, exist_ok=True)

    if settings.preload_embedding_model:
        await warm_embedding_backend()

    # Pull required Ollama models if the configured runtime still depends on Ollama
    if (
        settings.uses_ollama_embeddings
        or settings.uses_ollama_generation
        or settings.uses_ollama_query_understanding
    ):
        await _ensure_ollama_models()
    else:
        logger.info("Skipping Ollama startup checks in cloud-compatible mode")

    # Rebuild BM25 index from existing ChromaDB data
    logger.info("Building BM25 index from existing chunks...")
    existing_chunks = chroma_store.get_all_chunks()
    if existing_chunks:
        bm25_index.build(existing_chunks)
        graph_index.build(existing_chunks)
        logger.info(f"BM25 + graph index ready with {len(existing_chunks)} chunks")
    else:
        logger.info("No existing chunks found — BM25 index empty (will build on first ingest)")

    logger.info("✅ Ethereal Engine ready")
    yield

    logger.info("Shutting down...")


async def _ensure_ollama_models():
    """Check Ollama is reachable and prompt model is available."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{settings.ollama_base_url}/api/tags")
            r.raise_for_status()
            available = [m["name"] for m in r.json().get("models", [])]
            logger.info(f"Ollama available models: {available}")

            for model in [settings.ollama_llm_model, settings.ollama_embed_model]:
                # Check if model or base name is available
                model_base = model.split(":")[0]
                if not any(model_base in m for m in available):
                    logger.warning(f"Model '{model}' not found. Pulling... (this may take a while)")
                    async with httpx.AsyncClient(timeout=600) as pull_client:
                        await pull_client.post(
                            f"{settings.ollama_base_url}/api/pull",
                            json={"name": model, "stream": False}
                        )
                    logger.info(f"✅ Model '{model}' pulled successfully")

    except Exception as e:
        logger.warning(f"Ollama not reachable at startup: {e}. Will retry on first request.")


# ══════════════════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="End-to-end RAG pipeline — Ethereal Engine",
    lifespan=lifespan,
)

# CORS — allow frontend origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all API routes under /api
app.include_router(router, prefix="/api")


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/api/health",
    }
