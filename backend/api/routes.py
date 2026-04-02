"""
Ethereal Engine — API Routes
All REST endpoints + WebSocket for real-time pipeline streaming.
"""
from __future__ import annotations

import os
import uuid
import time
import asyncio
import json
from pathlib import Path
from typing import Optional

import aiofiles
import httpx
from fastapi import APIRouter, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect, Form
from fastapi.responses import StreamingResponse
from loguru import logger

from core.config import get_settings
from core.models import (
    IngestURLRequest, IngestAPIRequest, QueryRequest,
    GenerationRequest, SystemStats, PipelineEvent, ChunkStrategy,
    FeedbackRequest, FeedbackRecord,
    EvalRequest, EvalReport, EvalResult,
    TraceRecord, TraceStep,
)
from ingestion.ingestor import ingest_file, ingest_url, ingest_api
from ingestion.chunker import chunk_document
from ingestion.enricher import enrich_document, enrich_chunk
from storage.store import chroma_store, bm25_index, query_cache, graph_index
from retrieval.retriever import retrieve
from generation.generator import (
    compute_confidence,
    extract_citations,
    generate,
    generate_stream,
)

settings = get_settings()
router  = APIRouter()

# Track active WebSocket connections
active_ws: dict[str, WebSocket] = {}

# Simple stats counters
_stats = {"queries": 0, "latencies": [], "cache_hits": 0, "total_requests": 0}

# In-memory stores for feedback and traces (persist across requests, reset on restart)
_feedback_store: list[FeedbackRecord] = []
_trace_store:    list[TraceRecord]    = []
_MAX_TRACES    = 500   # rolling window
_MODEL_CACHE_TTL_SECONDS = 60
_generation_model_catalog_cache: dict[str, object] = {
    "fetched_at": 0.0,
    "data": None,
}


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


async def _get_ollama_model_catalog() -> dict:
    catalog = {
        "models": [],
        "chat_models": [],
        "embedding_models": [],
        "capabilities": {},
        "providers": {},
        "default_chat_model": settings.ollama_llm_model,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{settings.ollama_base_url}/api/tags")
        response.raise_for_status()
        models = [m["name"] for m in response.json().get("models", [])]
        catalog["models"] = models

        for model_name in models:
            capabilities: list[str] = []
            try:
                show_response = await client.post(
                    f"{settings.ollama_base_url}/api/show",
                    json={"name": model_name},
                )
                show_response.raise_for_status()
                show_data = show_response.json()
                capabilities = show_data.get("capabilities") or []
            except Exception as exc:
                logger.warning(f"Failed to inspect Ollama model '{model_name}': {exc}")

            catalog["capabilities"][model_name] = capabilities
            catalog["providers"][model_name] = "ollama"

            if any(cap in capabilities for cap in ("completion", "tools")):
                catalog["chat_models"].append(model_name)
            if "embedding" in capabilities:
                catalog["embedding_models"].append(model_name)

        if not catalog["chat_models"]:
            catalog["chat_models"] = [
                model_name
                for model_name in models
                if model_name not in catalog["embedding_models"]
            ]

        if settings.ollama_llm_model not in catalog["chat_models"]:
            llm_with_latest = (
                f"{settings.ollama_llm_model}:latest"
                if ":" not in settings.ollama_llm_model
                else settings.ollama_llm_model
            )
            if llm_with_latest in catalog["chat_models"]:
                catalog["default_chat_model"] = llm_with_latest
            elif catalog["chat_models"]:
                catalog["default_chat_model"] = catalog["chat_models"][0]
        else:
            catalog["default_chat_model"] = settings.ollama_llm_model

    return catalog


async def _get_groq_model_catalog() -> dict:
    catalog = {
        "models": [],
        "chat_models": [],
        "embedding_models": [],
        "capabilities": {},
        "providers": {},
        "default_chat_model": settings.groq_model,
    }

    if not settings.groq_enabled:
        return catalog

    async with httpx.AsyncClient(
        timeout=settings.groq_timeout,
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
    ) as client:
        response = await client.get(f"{settings.groq_base_url}/models")
        response.raise_for_status()
        raw_models = [
            item.get("id", "")
            for item in response.json().get("data", [])
            if item.get("id")
        ]

    chat_models = [
        model_name
        for model_name in raw_models
        if not model_name.startswith(("whisper", "distil-whisper", "playai-tts", "tts"))
    ]
    if settings.groq_model:
        chat_models = [settings.groq_model, *chat_models]
    chat_models = _dedupe_preserve_order(chat_models)

    catalog["models"] = chat_models
    catalog["chat_models"] = chat_models
    if chat_models:
        catalog["default_chat_model"] = (
            settings.groq_model if settings.groq_model in chat_models else chat_models[0]
        )

    for model_name in chat_models:
        catalog["capabilities"][model_name] = ["completion"]
        catalog["providers"][model_name] = "groq"

    return catalog


async def _get_generation_model_catalog(force_refresh: bool = False) -> dict:
    now = time.time()
    cached = _generation_model_catalog_cache.get("data")
    fetched_at = float(_generation_model_catalog_cache.get("fetched_at") or 0.0)
    if (
        not force_refresh
        and cached is not None
        and (now - fetched_at) < _MODEL_CACHE_TTL_SECONDS
    ):
        return cached

    preferred_provider = settings.preferred_generation_provider
    merged = {
        "models": [],
        "chat_models": [],
        "embedding_models": [],
        "capabilities": {},
        "providers": {},
        "default_chat_model": settings.groq_model if preferred_provider == "groq" else settings.ollama_llm_model,
        "active_generation_provider": preferred_provider,
        "groq_enabled": settings.groq_enabled,
        "errors": {},
    }

    ollama_catalog = None
    groq_catalog = None

    try:
        ollama_catalog = await _get_ollama_model_catalog()
    except Exception as exc:
        merged["errors"]["ollama"] = str(exc)
        logger.warning(f"Failed to load Ollama model catalog: {exc}")

    if settings.groq_enabled:
        try:
            groq_catalog = await _get_groq_model_catalog()
        except Exception as exc:
            merged["errors"]["groq"] = str(exc)
            logger.warning(f"Failed to load Groq model catalog: {exc}")
            groq_catalog = {
                "models": [settings.groq_model],
                "chat_models": [settings.groq_model],
                "embedding_models": [],
                "capabilities": {settings.groq_model: ["completion"]},
                "providers": {settings.groq_model: "groq"},
                "default_chat_model": settings.groq_model,
            }

    preferred_catalogs = (
        [groq_catalog, ollama_catalog]
        if preferred_provider == "groq"
        else [ollama_catalog, groq_catalog]
    )

    for catalog in preferred_catalogs:
        if not catalog:
            continue
        merged["models"].extend(catalog.get("models", []))
        merged["chat_models"].extend(catalog.get("chat_models", []))
        merged["embedding_models"].extend(catalog.get("embedding_models", []))
        merged["capabilities"].update(catalog.get("capabilities", {}))
        merged["providers"].update(catalog.get("providers", {}))

    merged["models"] = _dedupe_preserve_order(merged["models"])
    merged["chat_models"] = _dedupe_preserve_order(merged["chat_models"])
    merged["embedding_models"] = _dedupe_preserve_order(merged["embedding_models"])

    if preferred_provider == "groq" and groq_catalog and groq_catalog.get("chat_models"):
        merged["default_chat_model"] = groq_catalog.get("default_chat_model") or groq_catalog["chat_models"][0]
    elif ollama_catalog and ollama_catalog.get("chat_models"):
        merged["default_chat_model"] = ollama_catalog.get("default_chat_model") or ollama_catalog["chat_models"][0]
    elif groq_catalog and groq_catalog.get("chat_models"):
        merged["default_chat_model"] = groq_catalog.get("default_chat_model") or groq_catalog["chat_models"][0]

    default_model = str(merged["default_chat_model"])
    merged["active_generation_provider"] = merged["providers"].get(default_model, preferred_provider)

    _generation_model_catalog_cache["data"] = merged
    _generation_model_catalog_cache["fetched_at"] = now
    return merged


async def _resolve_generation_model(requested_model: Optional[str]) -> tuple[str, str]:
    catalog = await _get_generation_model_catalog()
    default_model = str(catalog.get("default_chat_model") or settings.ollama_llm_model)
    default_provider = str(
        catalog.get("providers", {}).get(default_model, settings.preferred_generation_provider)
    )

    if not requested_model:
        return default_provider, default_model

    if requested_model in catalog.get("chat_models", []):
        return str(catalog.get("providers", {}).get(requested_model, default_provider)), requested_model

    if requested_model in catalog.get("embedding_models", []):
        logger.warning(
            f"Requested generation model '{requested_model}' does not support chat. "
            f"Falling back to '{default_model}'."
        )
        return default_provider, default_model

    logger.warning(
        f"Requested generation model '{requested_model}' is unavailable. "
        f"Falling back to '{default_model}'."
    )
    return default_provider, default_model


# ══════════════════════════════════════════════════════════════════════════════
# Health & Status
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/health")
async def health():
    return {"status": "ok", "version": settings.app_version, "timestamp": time.time()}


@router.get("/stats", response_model=SystemStats)
async def get_stats():
    import httpx

    # Check Ollama
    ollama_status = "local"
    if (
        settings.uses_ollama_embeddings
        or settings.uses_ollama_generation
        or settings.uses_ollama_query_understanding
    ):
        ollama_status = "unknown"
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{settings.ollama_base_url}/api/tags")
                ollama_status = "healthy" if r.status_code == 200 else "degraded"
        except Exception:
            ollama_status = "offline"

    chroma_info = chroma_store.get_stats()
    redis_status = await query_cache.status()

    avg_latency = (sum(_stats["latencies"]) / len(_stats["latencies"])
                   if _stats["latencies"] else 0)
    cache_rate  = (_stats["cache_hits"] / _stats["total_requests"]
                   if _stats["total_requests"] else 0)

    return SystemStats(
        total_documents=chroma_info.get("source_count", 0),
        total_chunks=chroma_info["chunk_count"],
        total_queries=_stats["queries"],
        avg_latency_ms=avg_latency,
        cache_hit_rate=cache_rate,
        active_websockets=len(active_ws),
        ollama_status=ollama_status,
        chroma_status=chroma_info["status"],
        redis_status=redis_status,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Phase 01 — Ingestion Routes
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/ingest/file")
async def ingest_file_endpoint(
    file: UploadFile = File(...),
    chunk_strategy: str = Form(default="semantic"),
    session_id: Optional[str] = Form(default=None),
):
    """Upload and ingest a PDF, DOCX, or TXT file."""
    # Validate file size
    if file.size and file.size > settings.max_file_size_mb * 1024 * 1024:
        raise HTTPException(413, f"File too large (max {settings.max_file_size_mb}MB)")

    # Save upload
    upload_dir = settings.upload_path_resolved
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_id   = str(uuid.uuid4())
    file_path = upload_dir / f"{file_id}_{file.filename}"

    async with aiofiles.open(file_path, 'wb') as f:
        content = await file.read()
        await f.write(content)

    _current_job.update({"status": "running", "message": "Starting ingestion…", "progress": 0, "total": 0})
    ws = active_ws.get(session_id) if session_id else None
    await _send_event(ws, "stage_start", "ingestion", f"Processing {file.filename}...")

    try:
        strategy = ChunkStrategy(chunk_strategy)

        # Ingest
        doc = await ingest_file(file_path)
        await _send_event(ws, "stage_done", "ingestion", f"Extracted {len(doc.content)} characters")
        _current_job.update({"message": "Chunking document…", "progress": 20})

        # Enrich document
        doc = await enrich_document(doc)
        await _send_event(ws, "stage_start", "chunking", f"Chunking with strategy: {strategy.value}")

        # Chunk
        chunks = chunk_document(doc, strategy=strategy)
        await _send_event(ws, "stage_done", "chunking", f"Created {len(chunks)} chunks")
        _current_job.update({"message": f"Enriching {len(chunks)} chunks…", "progress": 40, "total": len(chunks)})

        # Enrich chunks (run concurrently — pure CPU, no Ollama calls)
        await _send_event(ws, "stage_start", "enrichment", "Extracting keywords and entities...")
        import asyncio as _asyncio
        enriched = list(await _asyncio.gather(*[enrich_chunk(c) for c in chunks]))
        await _send_event(ws, "stage_done", "enrichment", f"Keywords and entities extracted from {len(enriched)} chunks")
        _current_job.update({"message": f"Embedding {len(enriched)} chunks via Ollama…", "progress": 55})

        # Embed + store  (this is the slow part — send estimated time)
        est_secs = max(5, len(enriched) * 2)
        await _send_event(ws, "stage_start", "embedding",
                          f"Generating embeddings for {len(enriched)} chunks "
                          f"(~{est_secs}s on CPU)…")
        await chroma_store.add_chunks(enriched)
        await _send_event(ws, "stage_done", "embedding", f"Stored {len(enriched)} vectors in ChromaDB")
        _current_job.update({"message": "Building indexes…", "progress": 90})

        # Rebuild BM25 + graph index
        all_chunks = chroma_store.get_all_chunks()
        bm25_index.build(all_chunks)
        graph_index.build(all_chunks)

        await _send_event(ws, "complete", "pipeline", "Ingestion complete!", {
            "doc_id": doc.id, "chunk_count": len(enriched),
            "file_name": file.filename,
        })
        _current_job.update({"status": "done", "message": "Ingestion complete!", "progress": 100})

        return {
            "success": True,
            "doc_id": doc.id,
            "file_name": file.filename,
            "chunk_count": len(enriched),
            "keywords": doc.metadata.keywords[:10],
            "entities": doc.metadata.entities[:10],
        }

    except Exception as e:
        await _send_event(ws, "error", "ingestion", str(e))
        _current_job.update({"status": "error", "message": str(e), "progress": 0})
        raise HTTPException(500, str(e))


@router.post("/ingest/url")
async def ingest_url_endpoint(request: IngestURLRequest, session_id: Optional[str] = None):
    """Crawl and ingest content from a URL."""
    ws = active_ws.get(session_id) if session_id else None
    await _send_event(ws, "stage_start", "ingestion", f"Crawling {request.url}...")

    docs = []
    async for doc in ingest_url(request.url, request.max_depth, request.max_pages):
        docs.append(doc)
        await _send_event(ws, "stage_done", "ingestion", f"Crawled: {doc.metadata.title or request.url}")

    if not docs:
        raise HTTPException(422, "No content could be extracted from the URL")

    total_chunks = 0
    for doc in docs:
        doc = await enrich_document(doc)
        chunks = chunk_document(doc)
        enriched = [await enrich_chunk(c) for c in chunks]
        await chroma_store.add_chunks(enriched)
        total_chunks += len(enriched)

    all_chunks = chroma_store.get_all_chunks()
    bm25_index.build(all_chunks)
    graph_index.build(all_chunks)

    await _send_event(ws, "complete", "pipeline", f"Ingested {len(docs)} pages", {
        "doc_count": len(docs), "chunk_count": total_chunks
    })

    return {"success": True, "doc_count": len(docs), "chunk_count": total_chunks}


@router.post("/ingest/api")
async def ingest_api_endpoint(request: IngestAPIRequest, session_id: Optional[str] = None):
    """Fetch and ingest data from a REST API endpoint."""
    ws = active_ws.get(session_id) if session_id else None
    await _send_event(ws, "stage_start", "ingestion", f"Fetching {request.endpoint}...")

    try:
        doc = await ingest_api(request.endpoint, request.method,
                                request.headers, request.body, request.json_path)
        doc = await enrich_document(doc)
        chunks = chunk_document(doc)
        enriched = [await enrich_chunk(c) for c in chunks]
        await chroma_store.add_chunks(enriched)

        all_chunks = chroma_store.get_all_chunks()
        bm25_index.build(all_chunks)
        graph_index.build(all_chunks)

        await _send_event(ws, "complete", "pipeline", f"API ingestion complete", {
            "chunk_count": len(enriched)
        })

        return {"success": True, "chunk_count": len(enriched)}
    except Exception as e:
        await _send_event(ws, "error", "ingestion", str(e))
        raise HTTPException(500, str(e))


@router.get("/ingest/sources")
async def list_sources():
    """List all ingested document sources."""
    collection = chroma_store.get_collection()
    if collection.count() == 0:
        return {"sources": []}

    results = collection.get(include=["metadatas"])
    sources = {}
    for meta in results["metadatas"]:
        src = meta.get("source", "unknown")
        if src not in sources:
            sources[src] = {
                "source": src,
                "title": meta.get("title", ""),
                "source_type": meta.get("source_type", ""),
                "chunk_count": 0,
                "created_at": meta.get("created_at", 0),
            }
        sources[src]["chunk_count"] += 1

    return {"sources": list(sources.values())}


# Running job state (simple in-memory — one job at a time)
_current_job: dict = {"status": "idle", "message": "", "progress": 0, "total": 0}


@router.get("/ingest/progress")
async def ingest_progress():
    """Poll-based progress fallback for when WebSocket isn't connected."""
    return _current_job


@router.delete("/ingest/source")
async def delete_source(source: str):
    """Delete all chunks from a specific source."""
    deleted = chroma_store.delete_by_source(source)
    all_chunks = chroma_store.get_all_chunks()
    bm25_index.build(all_chunks)
    graph_index.build(all_chunks)
    return {"deleted_chunks": deleted}


# ══════════════════════════════════════════════════════════════════════════════
# Phase 03 — Retrieval Routes
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/retrieve")
async def retrieve_endpoint(request: QueryRequest):
    """Retrieve relevant chunks for a query."""
    _stats["total_requests"] += 1
    start = time.time()

    t0 = time.time()
    understanding, results = await retrieve(request)
    retrieval_ms = int((time.time() - t0) * 1000)

    elapsed = int((time.time() - start) * 1000)
    _stats["latencies"].append(elapsed)
    _stats["queries"] += 1

    # Record trace
    _record_trace(
        query=request.query,
        steps=[TraceStep(stage="retrieval", duration_ms=retrieval_ms,
                         detail=f"{len(results)} chunks via hybrid+rerank")],
        total_ms=elapsed,
        chunk_count=len(results),
    )

    return {
        "understanding": understanding.model_dump(),
        "results": [
            {
                "chunk_id": r.chunk.id,
                "doc_id": r.chunk.doc_id,
                "content": r.chunk.content,
                "source": r.chunk.metadata.source,
                "source_type": r.chunk.metadata.source_type,
                "title": r.chunk.metadata.title,
                "score": r.score,
                "rerank_score": r.rerank_score,
                "retrieval_method": r.retrieval_method,
                "keywords": r.chunk.metadata.keywords,
                "summary": r.chunk.metadata.summary,
                "token_count": r.chunk.token_count,
                "chunk_index": r.chunk.chunk_index,
            }
            for r in results
        ],
        "context_chunks": [r.model_dump() for r in results],
        "latency_ms": elapsed,
        "result_count": len(results),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Phase 04 — Generation Routes
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/generate")
async def generate_endpoint(request: GenerationRequest):
    """Full RAG: retrieve + generate (non-streaming)."""
    _stats["total_requests"] += 1
    start = time.time()
    selected_provider, selected_model = await _resolve_generation_model(request.model)

    if request.context_chunks:
        chunks = request.context_chunks
        retrieval_ms = 0
    else:
        t0 = time.time()
        query_req = QueryRequest(
            query=request.query,
            chat_history=request.chat_history,
            top_k=settings.rerank_top_k,
        )
        _, chunks = await retrieve(query_req)
        retrieval_ms = int((time.time() - t0) * 1000)

    # Generate
    t1 = time.time()
    try:
        response = await generate(
            query=request.query,
            chunks=chunks,
            chat_history=request.chat_history,
            system_prompt=request.system_prompt,
            model=selected_model,
            provider=selected_provider,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
    except Exception as e:
        logger.error(
            f"Generation failed for model '{selected_provider}:{selected_model}': {e}"
        )
        raise HTTPException(502, str(e))
    generation_ms = int((time.time() - t1) * 1000)

    elapsed = int((time.time() - start) * 1000)
    _stats["latencies"].append(elapsed)
    _stats["queries"] += 1

    # Record trace
    _record_trace(
        query=request.query,
        steps=[
            TraceStep(stage="retrieval",  duration_ms=retrieval_ms,  detail=f"{len(chunks)} chunks"),
            TraceStep(
                stage="generation",
                duration_ms=generation_ms,
                detail=f"provider={selected_provider}, model={selected_model}",
            ),
        ],
        total_ms=elapsed,
        chunk_count=len(chunks),
    )

    return response


@router.post("/generate/stream")
async def generate_stream_endpoint(request: GenerationRequest):
    """Full RAG with streaming token response."""
    selected_provider, selected_model = await _resolve_generation_model(request.model)
    if request.context_chunks:
        chunks = request.context_chunks
    else:
        query_req = QueryRequest(query=request.query, chat_history=request.chat_history)
        _, chunks = await retrieve(query_req)

    async def token_stream():
        start = time.time()
        full_answer = ""
        try:
            async for token in generate_stream(
                query=request.query,
                chunks=chunks,
                chat_history=request.chat_history,
                system_prompt=request.system_prompt,
                model=selected_model,
                provider=selected_provider,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            ):
                full_answer += token
                yield f"data: {json.dumps({'token': token})}\n\n"

            citations = extract_citations(full_answer, chunks)
            confidence = compute_confidence(full_answer, chunks)
            yield (
                "data: "
                + json.dumps(
                    {
                        "done": True,
                        "meta": {
                            "answer": full_answer.strip(),
                            "citations": [citation.model_dump() for citation in citations],
                            "confidence": confidence,
                            "latency_ms": int((time.time() - start) * 1000),
                            "model": selected_model,
                            "provider": selected_provider,
                            "token_usage": {
                                "estimated_prompt_tokens": sum(
                                    c.chunk.token_count for c in chunks
                                ),
                                "estimated_completion_tokens": len(full_answer.split()),
                            },
                        },
                    }
                )
                + "\n\n"
            )
        except Exception as exc:
            logger.error(
                f"Streaming generation failed for model '{selected_provider}:{selected_model}': {exc}"
            )
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(token_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/models")
async def list_models():
    """List available generation and embedding models."""
    try:
        catalog = await _get_generation_model_catalog(force_refresh=True)
        return catalog
    except Exception as e:
        return {
            "models": [],
            "chat_models": [],
            "embedding_models": [],
            "capabilities": {},
            "providers": {},
            "default_chat_model": settings.ollama_llm_model,
            "active_generation_provider": settings.preferred_generation_provider,
            "groq_enabled": settings.groq_enabled,
            "errors": {"models": str(e)},
            "error": str(e),
        }


# ══════════════════════════════════════════════════════════════════════════════
# Feedback Loop
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/feedback", response_model=FeedbackRecord)
async def submit_feedback(request: FeedbackRequest):
    """
    Record user feedback (thumbs up/down) for a query+answer pair.
    Used to drive the feedback loop shown in the architecture diagram.
    """
    record = FeedbackRecord(**request.model_dump())
    _feedback_store.append(record)
    logger.info(f"Feedback recorded: {record.rating} for query='{record.query[:60]}'")
    return record


@router.get("/feedback")
async def list_feedback(limit: int = 50, rating: Optional[str] = None):
    """Return recent feedback records, optionally filtered by rating."""
    records = _feedback_store[-limit:]
    if rating:
        records = [r for r in records if r.rating == rating]
    total_up   = sum(1 for r in _feedback_store if r.rating == "up")
    total_down = sum(1 for r in _feedback_store if r.rating == "down")
    return {
        "total": len(_feedback_store),
        "thumbs_up": total_up,
        "thumbs_down": total_down,
        "satisfaction_rate": round(total_up / max(total_up + total_down, 1), 3),
        "records": [r.model_dump() for r in records],
    }


@router.delete("/feedback")
async def clear_feedback():
    """Clear all feedback records."""
    count = len(_feedback_store)
    _feedback_store.clear()
    return {"cleared": count}


# ══════════════════════════════════════════════════════════════════════════════
# Evaluation
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/eval", response_model=EvalReport)
async def run_evaluation(request: EvalRequest):
    """
    Run a batch evaluation over question/answer pairs.
    Measures source recall and answer similarity for each sample.
    """
    results: list[EvalResult] = []
    total_latency = 0

    for sample in request.samples:
        t0 = time.time()
        try:
            query_req = QueryRequest(query=sample.question, top_k=request.top_k)
            understanding, chunks = await retrieve(query_req)

            gen_resp = await generate(
                query=sample.question,
                chunks=chunks,
                chat_history=[],
            )

            latency_ms     = int((time.time() - t0) * 1000)
            retrieved_srcs = list({c.chunk.metadata.source for c in chunks})
            actual_answer  = gen_resp.answer

            # Source recall: fraction of expected sources that were retrieved
            if sample.expected_sources:
                hits = sum(
                    1 for exp in sample.expected_sources
                    if any(exp.lower() in src.lower() for src in retrieved_srcs)
                )
                source_recall = hits / len(sample.expected_sources)
            else:
                source_recall = 1.0  # no expected sources → not penalised

            # Answer similarity: naive word-overlap (Jaccard on words)
            expected_words = set(sample.expected_answer.lower().split())
            actual_words   = set(actual_answer.lower().split())
            if expected_words | actual_words:
                answer_sim = len(expected_words & actual_words) / len(expected_words | actual_words)
            else:
                answer_sim = 0.0

            passed = source_recall >= 0.5 and answer_sim >= 0.3

        except Exception as e:
            logger.error(f"Eval sample failed: {e}")
            latency_ms     = int((time.time() - t0) * 1000)
            retrieved_srcs = []
            actual_answer  = f"ERROR: {e}"
            source_recall  = 0.0
            answer_sim     = 0.0
            passed         = False

        total_latency += latency_ms
        results.append(EvalResult(
            question=sample.question,
            expected=sample.expected_answer,
            actual=actual_answer,
            sources_retrieved=retrieved_srcs,
            expected_sources=sample.expected_sources,
            source_recall=round(source_recall, 3),
            answer_similarity=round(answer_sim, 3),
            latency_ms=latency_ms,
            passed=passed,
        ))

    total   = len(results)
    passed  = sum(1 for r in results if r.passed)
    return EvalReport(
        total=total,
        passed=passed,
        pass_rate=round(passed / max(total, 1), 3),
        avg_latency_ms=round(total_latency / max(total, 1), 1),
        avg_source_recall=round(sum(r.source_recall for r in results) / max(total, 1), 3),
        avg_answer_similarity=round(sum(r.answer_similarity for r in results) / max(total, 1), 3),
        results=results,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Traces (Observability)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/traces")
async def list_traces(limit: int = 50, session_id: Optional[str] = None):
    """Return recent per-request traces for observability."""
    records = _trace_store[-limit:]
    if session_id:
        records = [r for r in records if r.session_id == session_id]
    return {
        "total_recorded": len(_trace_store),
        "traces": [r.model_dump() for r in records],
    }


@router.delete("/traces")
async def clear_traces():
    count = len(_trace_store)
    _trace_store.clear()
    return {"cleared": count}


# ══════════════════════════════════════════════════════════════════════════════
# WebSocket — Real-time Pipeline Events
# ══════════════════════════════════════════════════════════════════════════════

@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket for live pipeline event streaming."""
    await websocket.accept()
    active_ws[session_id] = websocket
    logger.info(f"WebSocket connected: {session_id}")

    try:
        # Send welcome event
        await websocket.send_json({
            "event_type": "connected",
            "stage": "system",
            "message": f"Ethereal Engine v{settings.app_version} ready",
            "timestamp": time.time(),
        })

        # Keep alive
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                # Echo back for ping/pong
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_json({"event_type": "heartbeat", "stage": "system",
                                           "message": "alive", "timestamp": time.time()})
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {session_id}")
    finally:
        active_ws.pop(session_id, None)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

async def _send_event(ws: Optional[WebSocket], event_type: str,
                       stage: str, message: str, data: dict | None = None):
    """Send a pipeline event over WebSocket if connected."""
    if ws is None:
        return
    try:
        await ws.send_json({
            "event_type": event_type,
            "stage": stage,
            "message": message,
            "data": data,
            "timestamp": time.time(),
        })
    except Exception:
        pass


def _record_trace(query: str, steps: list, total_ms: int,
                  chunk_count: int, session_id: Optional[str] = None) -> None:
    """Append a trace record to the rolling in-memory store."""
    record = TraceRecord(
        query=query,
        steps=steps,
        total_ms=total_ms,
        chunk_count=chunk_count,
        session_id=session_id,
    )
    _trace_store.append(record)
    if len(_trace_store) > _MAX_TRACES:
        _trace_store.pop(0)
