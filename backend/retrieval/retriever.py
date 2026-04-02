"""
Ethereal Engine — Retrieval Pipeline
Phase 03: Retrieval

Handles:
  • Query understanding: rewrite, entity/intent detection, expansion
  • Hybrid retrieval: vector + BM25
  • Post-retrieval: reranking, deduplication, recency filtering,
                    permission filtering, context compression, chunk stitching
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

import httpx
from flashrank import Ranker, RerankRequest
from flashrank.Config import model_file_map
from loguru import logger

from core.config import get_settings
from core.models import (
    QueryRequest, RetrievedChunk, QueryUnderstanding, Chunk
)
from storage.store import chroma_store, bm25_index, query_cache, embed_text, graph_index

settings = get_settings()

# FlashRank reranker (runs locally, no API needed)
_reranker: Optional[Ranker] = None
_FLASHRANK_MODEL = "ms-marco-MiniLM-L-12-v2"
_FLASHRANK_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "flashrank"

def get_reranker() -> Ranker:
    global _reranker
    if _reranker is None:
        _FLASHRANK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _reranker = Ranker(
            model_name=_FLASHRANK_MODEL,
            cache_dir=str(_FLASHRANK_CACHE_DIR),
        )
    return _reranker


def flashrank_model_available() -> bool:
    model_file = model_file_map[_FLASHRANK_MODEL]
    return (_FLASHRANK_CACHE_DIR / _FLASHRANK_MODEL / model_file).exists()


# ══════════════════════════════════════════════════════════════════════════════
# Query Understanding
# ══════════════════════════════════════════════════════════════════════════════

INTENT_PATTERNS = {
    "definition":   re.compile(r'\bwhat is\b|\bdefine\b|\bexplain\b', re.I),
    "comparison":   re.compile(r'\bvs\b|\bcompare\b|\bdifference\b|\bbetter\b', re.I),
    "procedural":   re.compile(r'\bhow to\b|\bsteps\b|\bprocess\b|\bguide\b', re.I),
    "factual":      re.compile(r'\bwho\b|\bwhen\b|\bwhere\b|\bwhich\b', re.I),
    "summarization":re.compile(r'\bsummarize\b|\boverview\b|\bbrief\b|\btldr\b', re.I),
}


def detect_intent(query: str) -> str:
    for intent, pattern in INTENT_PATTERNS.items():
        if pattern.search(query):
            return intent
    return "general"


def expand_query(query: str, entities: list[str]) -> str:
    """Add entity synonyms and expand abbreviations."""
    # Simple expansion: append detected entities as additional search terms
    expansions = [e.split(":", 1)[-1] for e in entities if ":" in e]
    if expansions:
        return f"{query} {' '.join(expansions[:3])}"
    return query


def build_rule_based_understanding(query: str) -> QueryUnderstanding:
    from ingestion.enricher import extract_entities, extract_keywords

    entities = extract_entities(query)
    keywords = extract_keywords(query, top_n=5)
    return QueryUnderstanding(
        original_query=query,
        rewritten_query=query,
        intent=detect_intent(query),
        entities=entities,
        keywords=keywords or query.split(),
    )


async def understand_query(query: str, chat_history: list[dict] | None = None) -> QueryUnderstanding:
    """
    Use Ollama to rewrite and understand the query.
    Falls back to rule-based understanding if Ollama is unavailable.
    """
    if settings.preferred_query_understanding_provider == "rule":
        return build_rule_based_understanding(query)

    history_ctx = ""
    if chat_history:
        last = chat_history[-3:]  # Last 3 turns
        history_ctx = "\n".join(f"{m['role']}: {m['content']}" for m in last)

    prompt = f"""You are a query understanding system for a RAG pipeline.

Given the user query (and optional chat history), return a JSON object with:
- "rewritten_query": improved, self-contained version of the query
- "intent": one of [definition, comparison, procedural, factual, summarization, general]
- "entities": list of key named entities or concepts in the query
- "keywords": list of important search keywords
- "time_awareness": null or a string like "recent", "2023", "historical"

Respond ONLY with valid JSON, no markdown, no explanation.

{f'Chat history:{chr(10)}{history_ctx}{chr(10)}' if history_ctx else ''}
Query: {query}"""

    try:
        async with httpx.AsyncClient(timeout=settings.ollama_timeout) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.ollama_llm_model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.0, "num_predict": 300},
                }
            )
            response.raise_for_status()
            text = response.json().get("response", "")
            import json
            parsed = json.loads(text)
            if parsed:
                return QueryUnderstanding(
                    original_query=query,
                    rewritten_query=parsed.get("rewritten_query", query),
                    intent=parsed.get("intent", detect_intent(query)),
                    entities=parsed.get("entities", []),
                    keywords=parsed.get("keywords", query.split()),
                    time_awareness=parsed.get("time_awareness"),
                )
    except Exception as e:
        logger.warning(f"Query understanding LLM failed, using rule-based: {e!r}")

    return build_rule_based_understanding(query)


# ══════════════════════════════════════════════════════════════════════════════
# Hybrid Retrieval
# ══════════════════════════════════════════════════════════════════════════════

async def retrieve_vector(query_embedding: list[float], top_k: int,
                          filters: dict | None = None) -> list[RetrievedChunk]:
    return await chroma_store.query(query_embedding, top_k=top_k, filters=filters)


def retrieve_bm25(query: str, top_k: int) -> list[tuple[str, float]]:
    return bm25_index.search(query, top_k=top_k)


async def hybrid_retrieve(understanding: QueryUnderstanding,
                           top_k: int = 10,
                           filters: dict | None = None) -> list[RetrievedChunk]:
    """
    Fuse vector + BM25 results using Reciprocal Rank Fusion (RRF).
    """
    search_query = expand_query(understanding.rewritten_query, understanding.entities)

    # Parallel retrieval
    query_embedding = await embed_text(search_query)
    vector_results  = await retrieve_vector(query_embedding, top_k=top_k * 2, filters=filters)
    bm25_results    = retrieve_bm25(search_query, top_k=top_k * 2)

    # RRF fusion (k=60 is standard)
    rrf_k = 60
    scores: dict[str, float] = {}

    for rank, result in enumerate(vector_results):
        cid = result.chunk.id
        scores[cid] = scores.get(cid, 0) + (1 / (rrf_k + rank + 1)) * settings.vector_weight

    bm25_id_set = {cid for cid, _ in bm25_results}
    for rank, (cid, _) in enumerate(bm25_results):
        scores[cid] = scores.get(cid, 0) + (1 / (rrf_k + rank + 1)) * settings.bm25_weight

    # Build unified result list
    chunk_map: dict[str, RetrievedChunk] = {r.chunk.id: r for r in vector_results}

    # Fetch BM25-only chunks from ChromaDB if not already in vector results
    bm25_only_ids = [cid for cid in bm25_id_set if cid not in chunk_map]
    if bm25_only_ids:
        all_chunks = chroma_store.get_collection().get(ids=bm25_only_ids, include=["documents", "metadatas"])
        from storage.store import _meta_to_chunk
        for i, cid in enumerate(all_chunks["ids"]):
            if cid not in chunk_map:
                chunk = _meta_to_chunk(cid, all_chunks["documents"][i], all_chunks["metadatas"][i])
                chunk_map[cid] = RetrievedChunk(chunk=chunk, score=0.0, retrieval_method="bm25")

    # Sort by RRF score
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    results = []
    for cid, rrf_score in ranked:
        if cid in chunk_map:
            r = chunk_map[cid]
            method = "hybrid" if (cid in bm25_id_set and r.retrieval_method == "vector") else r.retrieval_method
            results.append(RetrievedChunk(chunk=r.chunk, score=rrf_score, retrieval_method=method))

    logger.info(f"Hybrid retrieval: {len(vector_results)} vector + {len(bm25_results)} BM25 → {len(results)} fused")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# Post-Retrieval Refinement
# ══════════════════════════════════════════════════════════════════════════════

def rerank(query: str, results: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """Cross-encoder reranking with FlashRank (runs locally)."""
    if not results:
        return results
    if not flashrank_model_available():
        return fallback_rerank(query, results, top_k)
    try:
        ranker = get_reranker()
        passages = [{"id": r.chunk.id, "text": r.chunk.content} for r in results]
        request  = RerankRequest(query=query, passages=passages)
        reranked = ranker.rerank(request)

        id_to_score = {item["id"]: item["score"] for item in reranked}
        for r in results:
            r.rerank_score = id_to_score.get(r.chunk.id, 0.0)

        results.sort(key=lambda r: r.rerank_score or 0, reverse=True)
        logger.info(f"Reranking complete, top chunk score: {results[0].rerank_score:.3f}")
        return results[:top_k]
    except Exception as e:
        logger.warning(f"Reranking failed: {e}")
        return fallback_rerank(query, results, top_k)


def fallback_rerank(query: str, results: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """Local heuristic reranking that requires no external model downloads."""
    query_terms = {
        token for token in re.findall(r"[a-z0-9]+", query.lower())
        if len(token) > 2
    }

    for result in results:
        haystack = " ".join(
            [
                result.chunk.metadata.title or "",
                result.chunk.metadata.source,
                result.chunk.content,
            ]
        ).lower()
        content_terms = set(re.findall(r"[a-z0-9]+", haystack))
        overlap = (
            len(query_terms & content_terms) / max(len(query_terms), 1)
            if query_terms else 0.0
        )
        phrase_bonus = 0.15 if query.lower() in haystack else 0.0
        result.rerank_score = min(1.0, (result.score * 0.65) + (overlap * 0.35) + phrase_bonus)

    results.sort(key=lambda item: item.rerank_score or 0, reverse=True)
    return results[:top_k]


def deduplicate(results: list[RetrievedChunk], similarity_threshold: float = 0.9) -> list[RetrievedChunk]:
    """Remove near-duplicate chunks using character overlap."""
    unique = []
    for result in results:
        is_dup = False
        for existing in unique:
            overlap = _jaccard_similarity(result.chunk.content, existing.chunk.content)
            if overlap >= similarity_threshold:
                is_dup = True
                break
        if not is_dup:
            unique.append(result)
    return unique


def _jaccard_similarity(a: str, b: str) -> float:
    set_a = set(a.lower().split())
    set_b = set(b.lower().split())
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def filter_by_recency(results: list[RetrievedChunk],
                       max_age_days: Optional[int] = None) -> list[RetrievedChunk]:
    """Remove chunks older than max_age_days if specified."""
    if not max_age_days:
        return results
    cutoff = time.time() - (max_age_days * 86400)
    return [r for r in results if r.chunk.metadata.created_at >= cutoff]


def filter_by_permission(results: list[RetrievedChunk],
                          user_permissions: list[str] = None) -> list[RetrievedChunk]:
    """Filter chunks based on permission metadata."""
    if not user_permissions:
        return [r for r in results if "public" in r.chunk.metadata.permissions]
    allowed = set(user_permissions) | {"public"}
    return [r for r in results if any(p in allowed for p in r.chunk.metadata.permissions)]


def stitch_chunks(results: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """
    Merge adjacent chunks from the same document for better context coherence.
    """
    from collections import defaultdict
    by_doc: dict[str, list[RetrievedChunk]] = defaultdict(list)
    for r in results:
        by_doc[r.chunk.doc_id].append(r)

    stitched = []
    for doc_id, chunks in by_doc.items():
        chunks.sort(key=lambda r: r.chunk.chunk_index)
        merged = [chunks[0]]
        for curr in chunks[1:]:
            prev = merged[-1]
            if curr.chunk.chunk_index == prev.chunk.chunk_index + 1:
                # Adjacent — merge content
                combined = prev.chunk.content + "\n" + curr.chunk.content
                merged[-1] = RetrievedChunk(
                    chunk=Chunk(
                        id=prev.chunk.id,
                        doc_id=doc_id,
                        content=combined,
                        metadata=prev.chunk.metadata,
                        chunk_index=prev.chunk.chunk_index,
                        token_count=prev.chunk.token_count + curr.chunk.token_count,
                    ),
                    score=max(prev.score, curr.score),
                    retrieval_method="stitched",
                    rerank_score=max(prev.rerank_score or 0, curr.rerank_score or 0) or None,
                )
            else:
                merged.append(curr)
        stitched.extend(merged)

    return stitched


def compress_context(results: list[RetrievedChunk], max_tokens: int) -> list[RetrievedChunk]:
    """Trim results to fit within max_tokens for the context window."""
    from ingestion.chunker import count_tokens
    selected = []
    total = 0
    for r in results:
        tokens = r.chunk.token_count or count_tokens(r.chunk.content)
        if total + tokens <= max_tokens:
            selected.append(r)
            total += tokens
        else:
            break
    return selected


# ══════════════════════════════════════════════════════════════════════════════
# Full Retrieval Pipeline
# ══════════════════════════════════════════════════════════════════════════════

async def retrieve(request: QueryRequest) -> tuple[QueryUnderstanding, list[RetrievedChunk]]:
    """
    Run the full retrieval pipeline:
    1. Query understanding
    2. Hybrid retrieval (vector + BM25)
    3. Post-retrieval: dedup → recency filter → permission filter → rerank → stitch → compress
    """
    # Check cache first
    cached = await query_cache.get(request.query, request.top_k)
    if cached:
        logger.info("Cache hit for query")
        understanding = QueryUnderstanding(**cached["understanding"])
        results = [RetrievedChunk(**r) for r in cached["results"]]
        return understanding, results

    if chroma_store.get_collection().count() == 0:
        return build_rule_based_understanding(request.query), []

    # 1. Query understanding
    understanding = await understand_query(request.query, request.chat_history)

    # 2. Hybrid retrieval
    results = await hybrid_retrieve(understanding, top_k=settings.retrieval_top_k,
                                    filters=request.filters)

    # 2b. Graph retrieval (if enabled and entities were detected)
    if request.use_graph and understanding.entities and graph_index.node_count > 0:
        graph_chunk_ids = graph_index.query(understanding.entities, top_k=settings.retrieval_top_k)
        if graph_chunk_ids:
            try:
                raw = chroma_store.get_collection().get(
                    ids=graph_chunk_ids, include=["documents", "metadatas"]
                )
                from storage.store import _meta_to_chunk
                existing_ids = {r.chunk.id for r in results}
                for i, cid in enumerate(raw["ids"]):
                    if cid not in existing_ids:
                        chunk = _meta_to_chunk(cid, raw["documents"][i], raw["metadatas"][i])
                        results.append(RetrievedChunk(chunk=chunk, score=0.5, retrieval_method="graph"))
                logger.info(f"Graph retrieval added {len(raw['ids'])} candidate chunks")
            except Exception as e:
                logger.warning(f"Graph retrieval failed: {e}")

    # 3. Post-retrieval pipeline
    results = deduplicate(results)
    results = filter_by_permission(results)
    if request.use_reranking:
        results = rerank(understanding.rewritten_query, results, top_k=settings.rerank_top_k)
    results = stitch_chunks(results)
    results = compress_context(results, settings.max_context_tokens)

    # Limit to requested top_k
    results = results[:request.top_k]

    # Cache the results
    await query_cache.set(
        request.query, request.top_k,
        {
            "understanding": understanding.model_dump(),
            "results": [r.model_dump() for r in results],
        }
    )

    return understanding, results
