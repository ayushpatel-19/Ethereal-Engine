"""
Ethereal Engine — Storage Layer
Phase 02: Embedding + Storage

Handles:
  • Generating embeddings via Ollama (nomic-embed-text)
  • Storing vectors in ChromaDB
  • Building BM25 keyword index for hybrid search
  • In-memory graph index for entity relationships
  • Redis cache for frequent queries
"""
from __future__ import annotations

import os
import json
import sqlite3
import time
import hashlib
from pathlib import Path
from typing import Optional

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

import chromadb
import httpx
import redis.asyncio as aioredis
import tiktoken
from chromadb.config import Settings as ChromaSettings
from rank_bm25 import BM25Okapi
from loguru import logger

from core.config import get_settings
from core.models import Chunk, RetrievedChunk

settings = get_settings()
_resolved_embed_model: Optional[str] = None
_embed_model_dimensions: dict[str, int] = {}
_embed_encoder = tiktoken.get_encoding("cl100k_base")
_resolved_embed_token_limits: dict[str, int] = {}
_local_embedder = None
_embed_token_limits: dict[str, list[int]] = {
    "all-minilm": [256, 224, 192, 160, 128, 96, 64],
}


def _embedding_dimension(embeddings: list[list[float]] | list[float] | None) -> Optional[int]:
    if not embeddings:
        return None
    if isinstance(embeddings[0], list):
        return len(embeddings[0])
    return len(embeddings)


async def _probe_model_dimension(model_name: str) -> Optional[int]:
    cached = _embed_model_dimensions.get(model_name)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/embed",
                json={"model": model_name, "input": ["dimension probe"]},
            )
            response.raise_for_status()
            payload = response.json()
            dimension = _embedding_dimension(payload.get("embeddings") or payload.get("embedding"))
            if dimension is not None:
                _embed_model_dimensions[model_name] = dimension
            return dimension
    except Exception:
        return None


def _normalize_model_name(model_name: str) -> str:
    return model_name.split(":", 1)[0]


def _token_limits_for_model(model_name: str) -> list[Optional[int]]:
    normalized = _normalize_model_name(model_name)
    limits = _embed_token_limits.get(normalized, [])
    resolved_limit = _resolved_embed_token_limits.get(normalized)

    if resolved_limit is not None:
        ordered_limits = [resolved_limit, *[limit for limit in limits if limit != resolved_limit]]
        return ordered_limits

    if limits:
        return limits

    return [None]


def _truncate_text_for_embedding(text: str, token_limit: Optional[int]) -> str:
    if token_limit is None:
        return text

    tokens = _embed_encoder.encode(text)
    if len(tokens) <= token_limit:
        return text
    return _embed_encoder.decode(tokens[:token_limit])


def _get_local_embedder():
    global _local_embedder
    if _local_embedder is None:
        from sentence_transformers import SentenceTransformer

        logger.info(
            f"Loading local embedding model '{settings.local_embed_model}' "
            "(first load may take a minute on cold start)..."
        )
        _local_embedder = SentenceTransformer(settings.local_embed_model)
    return _local_embedder


def _probe_local_model_dimension() -> Optional[int]:
    cached = _embed_model_dimensions.get(settings.local_embed_model)
    if cached is not None:
        return cached

    try:
        embedder = _get_local_embedder()
        dimension = embedder.get_sentence_embedding_dimension()
        if dimension is not None:
            _embed_model_dimensions[settings.local_embed_model] = int(dimension)
            return int(dimension)
    except Exception as exc:
        logger.warning(f"Unable to inspect local embedding dimension: {exc}")
    return None


async def _embed_batch_local(texts: list[str], batch_size: int = 16) -> list[list[float]]:
    import asyncio

    embedder = _get_local_embedder()

    def _encode() -> list[list[float]]:
        return embedder.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).tolist()

    embeddings = await asyncio.to_thread(_encode)
    global _resolved_embed_model
    _resolved_embed_model = settings.local_embed_model
    dimension = _embedding_dimension(embeddings)
    if dimension is not None:
        _embed_model_dimensions[settings.local_embed_model] = dimension
    logger.info(
        f"✅ Embedded {len(texts)} texts → {len(embeddings)} vectors via local model "
        f"'{settings.local_embed_model}'"
    )
    return embeddings


async def warm_embedding_backend() -> None:
    if settings.preferred_embedding_provider == "local":
        import asyncio

        await asyncio.to_thread(_get_local_embedder)
        _probe_local_model_dimension()
        logger.info(
            f"Local embedding backend ready: '{settings.local_embed_model}'"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Embeddings via Ollama
# ══════════════════════════════════════════════════════════════════════════════

async def embed_text(text: str) -> list[float]:
    """Generate a single embedding via Ollama (new /api/embed endpoint with fallback)."""
    result = await embed_batch([text])
    return result[0]


async def embed_batch(texts: list[str], batch_size: int = 16) -> list[list[float]]:
    """
    Embed texts using Ollama's native batch endpoint (/api/embed).
    Falls back to parallel asyncio requests if the batch endpoint is unavailable.
    Processes in chunks of `batch_size` to avoid overwhelming Ollama on low-RAM machines.
    """
    import asyncio

    if not texts:
        return []

    if settings.preferred_embedding_provider == "local":
        return await _embed_batch_local(texts, batch_size=batch_size)

    all_embeddings: list[list[float]] = []

    def candidate_models() -> list[str]:
        configured = settings.ollama_embed_model
        fallback_models = [
            configured,
            f"{configured}:latest" if ":" not in configured else configured,
            "nomic-embed-text",
            "nomic-embed-text:latest",
            "all-minilm",
            "all-minilm:latest",
        ]
        if _resolved_embed_model:
            fallback_models.insert(0, _resolved_embed_model)

        ordered = []
        seen = set()
        for model in fallback_models:
            if model and model not in seen:
                ordered.append(model)
                seen.add(model)
        return ordered

    async def ordered_candidate_models() -> list[str]:
        models = candidate_models()
        collection_dimension = chroma_store.get_collection_dimension()
        if collection_dimension is None:
            return models

        matching: list[str] = []
        non_matching: list[str] = []
        for model_name in models:
            model_dimension = await _probe_model_dimension(model_name)
            if model_dimension == collection_dimension:
                matching.append(model_name)
            else:
                non_matching.append(model_name)

        if matching:
            if matching[0] != settings.ollama_embed_model:
                logger.warning(
                    f"Existing Chroma collection uses {collection_dimension}-d embeddings; "
                    f"prioritizing compatible Ollama model(s): {matching}. Delete "
                    f"'{settings.chroma_path_resolved}' to switch back to "
                    f"'{settings.ollama_embed_model}'."
                )
            return matching

        logger.warning(
            f"Existing Chroma collection uses {collection_dimension}-d embeddings, but no "
            f"configured fallback model matched that size. Writes will fail until you clear "
            f"'{settings.chroma_path_resolved}' or install a compatible embedding model."
        )
        return models

    async def _embed_with_legacy_endpoint(client: httpx.AsyncClient, model_name: str, batch: list[str]) -> list[list[float]]:
        concurrency = 4
        results: list[list[float]] = []
        for j in range(0, len(batch), concurrency):
            sub = batch[j : j + concurrency]
            responses = await asyncio.gather(*[
                client.post(
                    f"{settings.ollama_base_url}/api/embeddings",
                    json={"model": model_name, "prompt": text},
                )
                for text in sub
            ])
            for response in responses:
                response.raise_for_status()
                results.append(response.json()["embedding"])
        return results

    def _is_context_length_error(response: httpx.Response) -> bool:
        if response.status_code != 400:
            return False
        try:
            payload = response.json()
        except Exception:
            payload = {}
        detail = json.dumps(payload).lower()
        return "context length" in detail

    ordered_models = await ordered_candidate_models()

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        total_batches = (len(texts) + batch_size - 1) // batch_size
        logger.info(f"Embedding batch {i // batch_size + 1}/{total_batches} "
                    f"({len(batch)} texts)…")

        errors: list[str] = []
        batch_embeddings: list[list[float]] | None = None

        for model_name in ordered_models:
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    limits = _token_limits_for_model(model_name)
                    for token_limit in limits:
                        prepared_batch = [
                            _truncate_text_for_embedding(text, token_limit)
                            for text in batch
                        ]
                        truncated_count = sum(
                            1 for original, prepared in zip(batch, prepared_batch)
                            if original != prepared
                        )
                        if truncated_count:
                            logger.warning(
                                f"Truncating {truncated_count}/{len(batch)} text(s) to "
                                f"~{token_limit} tokens for embedding model '{model_name}'."
                            )

                        resp = await client.post(
                            f"{settings.ollama_base_url}/api/embed",
                            json={"model": model_name, "input": prepared_batch, "truncate": True},
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            embeddings = data.get("embeddings") or data.get("embedding")
                            if embeddings and isinstance(embeddings[0], list):
                                batch_embeddings = embeddings
                            elif embeddings:
                                batch_embeddings = [embeddings]
                            if batch_embeddings is not None:
                                if token_limit is not None:
                                    _resolved_embed_token_limits[_normalize_model_name(model_name)] = token_limit
                                break

                        errors.append(f"/api/embed {model_name}: HTTP {resp.status_code} {resp.text[:160]}")
                        if not _is_context_length_error(resp):
                            break

                    if batch_embeddings is None:
                        batch_embeddings = await _embed_with_legacy_endpoint(client, model_name, batch)

                global _resolved_embed_model
                _resolved_embed_model = model_name
                if model_name != settings.ollama_embed_model:
                    logger.warning(
                        f"Embedding model fallback active: using '{model_name}' "
                        f"instead of '{settings.ollama_embed_model}'"
                    )
                break
            except Exception as e:
                batch_embeddings = None
                errors.append(f"{model_name}: {e}")
                logger.warning(f"Embedding attempt failed for model '{model_name}': {e}")

        if batch_embeddings is None:
            raise RuntimeError(
                "All Ollama embedding attempts failed. " + " | ".join(errors[-6:])
            )

        all_embeddings.extend(batch_embeddings)

    logger.info(f"✅ Embedded {len(texts)} texts → {len(all_embeddings)} vectors")
    return all_embeddings


# ══════════════════════════════════════════════════════════════════════════════
# ChromaDB Client
# ══════════════════════════════════════════════════════════════════════════════

class ChromaStore:
    def __init__(self):
        self._client: Optional[chromadb.PersistentClient] = None
        self._collection = None
        self._collection_dimension: Optional[int] = None

    def _get_client(self):
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=str(settings.chroma_path_resolved),
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    chroma_product_telemetry_impl="storage.chroma_telemetry.NullTelemetry",
                    chroma_telemetry_impl="storage.chroma_telemetry.NullTelemetry",
                ),
            )
        return self._client

    def get_collection(self):
        if self._collection is None:
            client = self._get_client()
            self._collection = client.get_or_create_collection(
                name=settings.chroma_collection,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def _read_collection_dimension_from_sqlite(self) -> Optional[int]:
        db_path = settings.chroma_path_resolved / "chroma.sqlite3"
        if not db_path.exists():
            return None

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(db_path)
            cursor = connection.cursor()
            cursor.execute(
                "SELECT dimension FROM collections WHERE name = ?",
                (settings.chroma_collection,),
            )
            row = cursor.fetchone()
            if row and row[0] is not None:
                return int(row[0])
        except Exception as exc:
            logger.warning(f"Unable to read collection dimension from sqlite: {exc}")
        finally:
            if connection is not None:
                connection.close()
        return None

    def get_collection_dimension(self, refresh: bool = False) -> Optional[int]:
        if self._collection_dimension is not None and not refresh:
            return self._collection_dimension

        sqlite_dimension = self._read_collection_dimension_from_sqlite()
        if sqlite_dimension is not None:
            self._collection_dimension = sqlite_dimension
            return sqlite_dimension

        try:
            collection = self.get_collection()
            sample = collection.peek(limit=1)
            dimension = _embedding_dimension(sample.get("embeddings"))
            if dimension is not None:
                self._collection_dimension = dimension
            return dimension
        except Exception as exc:
            logger.warning(f"Unable to inspect collection dimension: {exc}")
            return None

    def _remember_collection_dimension(self, dimension: Optional[int]) -> None:
        self._collection_dimension = dimension

    async def add_chunks(self, chunks: list[Chunk]) -> None:
        """Embed and store chunks in ChromaDB."""
        if not chunks:
            return

        texts = [c.content for c in chunks]
        embeddings = await embed_batch(texts)
        embedding_dimension = _embedding_dimension(embeddings)
        collection_dimension = self.get_collection_dimension()

        if (
            collection_dimension is not None
            and embedding_dimension is not None
            and embedding_dimension != collection_dimension
        ):
            active_model = _resolved_embed_model or (
                settings.local_embed_model
                if settings.preferred_embedding_provider == "local"
                else settings.ollama_embed_model
            )
            raise RuntimeError(
                f"Embedding model '{active_model}' returned {embedding_dimension}-d vectors, "
                f"but the existing Chroma collection '{settings.chroma_collection}' in "
                f"'{settings.chroma_path_resolved}' is {collection_dimension}-d. Clear the existing "
                "Chroma data or switch back to a compatible embedding model."
            )

        collection = self.get_collection()
        collection.upsert(
            ids=[c.id for c in chunks],
            embeddings=embeddings,
            documents=texts,
            metadatas=[{
                "doc_id":      c.doc_id,
                "source":      c.metadata.source,
                "source_type": c.metadata.source_type.value,
                "title":       c.metadata.title or "",
                "chunk_index": c.chunk_index,
                "token_count": c.token_count,
                "keywords":    json.dumps(c.metadata.keywords),
                "entities":    json.dumps(c.metadata.entities),
                "summary":     c.metadata.summary or "",
                "language":    c.metadata.language or "en",
                "created_at":  c.metadata.created_at,
                "permissions": json.dumps(c.metadata.permissions),
                "parent_id":   c.parent_chunk_id or "",
            } for c in chunks],
        )
        self._remember_collection_dimension(embedding_dimension)

        # Store embeddings back on chunk objects
        for chunk, emb in zip(chunks, embeddings):
            chunk.embedding = emb

        logger.info(f"Stored {len(chunks)} chunks in ChromaDB")

    async def query(self, query_embedding: list[float], top_k: int = 10,
                    filters: dict | None = None) -> list[RetrievedChunk]:
        """Vector similarity search."""
        collection_dimension = self.get_collection_dimension()
        query_dimension = len(query_embedding) if query_embedding else None
        if (
            collection_dimension is not None
            and query_dimension is not None
            and query_dimension != collection_dimension
        ):
            active_model = _resolved_embed_model or (
                settings.local_embed_model
                if settings.preferred_embedding_provider == "local"
                else settings.ollama_embed_model
            )
            raise RuntimeError(
                f"Query embedding model '{active_model}' produced {query_dimension}-d vectors, "
                f"but the Chroma collection expects {collection_dimension}-d vectors."
            )

        collection = self.get_collection()
        where = _build_where_clause(filters) if filters else None

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, collection.count() or 1),
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        retrieved = []
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            distance = results["distances"][0][i]
            score = 1 - distance  # Convert distance to similarity

            chunk = _meta_to_chunk(doc_id, results["documents"][0][i], meta)
            retrieved.append(RetrievedChunk(
                chunk=chunk,
                score=score,
                retrieval_method="vector",
            ))

        return retrieved

    def get_all_chunks(self) -> list[Chunk]:
        """Return stored chunks with metadata for index rebuilding."""
        collection = self.get_collection()
        count = collection.count()
        if count == 0:
            return []
        results = collection.get(include=["documents", "metadatas"])
        return [
            _meta_to_chunk(chunk_id, document, metadata)
            for chunk_id, document, metadata in zip(
                results["ids"],
                results["documents"],
                results["metadatas"],
            )
        ]

    def get_stats(self) -> dict:
        try:
            collection = self.get_collection()
            count = collection.count()
            source_count = 0
            if count:
                results = collection.get(include=["metadatas"])
                source_count = len({meta.get("source", "unknown") for meta in results["metadatas"]})
            return {"chunk_count": count, "source_count": source_count, "status": "healthy"}
        except Exception as e:
            return {"chunk_count": 0, "source_count": 0, "status": f"error: {e}"}

    def delete_by_source(self, source: str) -> int:
        collection = self.get_collection()
        results = collection.get(where={"source": source})
        if results["ids"]:
            collection.delete(ids=results["ids"])
        return len(results["ids"])


def _build_where_clause(filters: dict) -> dict:
    """Convert user filters to ChromaDB where clause."""
    if not filters:
        return {}
    clauses = []
    for key, value in filters.items():
        if key in ("source_type", "language", "source"):
            clauses.append({key: {"$eq": value}})
    if len(clauses) == 1:
        return clauses[0]
    elif len(clauses) > 1:
        return {"$and": clauses}
    return {}


def _meta_to_chunk(chunk_id: str, text: str, meta: dict) -> Chunk:
    """Reconstruct a Chunk from ChromaDB metadata."""
    from core.models import DocumentMetadata, SourceType
    return Chunk(
        id=chunk_id,
        doc_id=meta.get("doc_id", ""),
        content=text,
        metadata=DocumentMetadata(
            source=meta.get("source", ""),
            source_type=SourceType(meta.get("source_type", "txt")),
            title=meta.get("title") or None,
            keywords=json.loads(meta.get("keywords", "[]")),
            entities=json.loads(meta.get("entities", "[]")),
            summary=meta.get("summary") or None,
            language=meta.get("language", "en"),
            created_at=meta.get("created_at", time.time()),
            permissions=json.loads(meta.get("permissions", '["public"]')),
        ),
        chunk_index=meta.get("chunk_index", 0),
        token_count=meta.get("token_count", 0),
        parent_chunk_id=meta.get("parent_id") or None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# BM25 Keyword Index
# ══════════════════════════════════════════════════════════════════════════════

class BM25Index:
    """In-memory BM25 index rebuilt from ChromaDB on startup."""

    def __init__(self):
        self._index: Optional[BM25Okapi] = None
        self._id_map: list[str] = []
        self._doc_map: dict[str, str] = {}

    def build(self, chunks: list[tuple[str, str]] | list[Chunk]) -> None:
        """Build BM25 index from stored chunks or raw (id, text) pairs."""
        if not chunks:
            self._index = None
            self._id_map = []
            self._doc_map = {}
            return

        if isinstance(chunks[0], Chunk):
            self._id_map = [chunk.id for chunk in chunks]
            self._doc_map = {chunk.id: chunk.content for chunk in chunks}
            tokenized = [chunk.content.lower().split() for chunk in chunks]
        else:
            self._id_map = [chunk_id for chunk_id, _ in chunks]
            self._doc_map = {chunk_id: text for chunk_id, text in chunks}
            tokenized = [text.lower().split() for _, text in chunks]

        self._index = BM25Okapi(tokenized)
        logger.info(f"BM25 index built with {len(self._id_map)} documents")

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Return (chunk_id, score) pairs."""
        if self._index is None or not self._id_map:
            return []
        tokenized_query = query.lower().split()
        scores = self._index.get_scores(tokenized_query)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(self._id_map[i], float(score)) for i, score in ranked[:top_k] if score > 0]

    def get_text(self, chunk_id: str) -> Optional[str]:
        return self._doc_map.get(chunk_id)


# ══════════════════════════════════════════════════════════════════════════════
# Redis Cache (with in-memory fallback for Windows / no-Redis setups)
# ══════════════════════════════════════════════════════════════════════════════

import time as _time

class QueryCache:
    """
    Query cache that uses Redis when available, otherwise falls back to an
    in-memory dict. The fallback respects the same TTL so behaviour is
    identical — just not shared across processes and not persisted.
    """

    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None
        self._redis_ok: Optional[bool] = None          # None = not yet tested
        self._mem: dict[str, tuple[str, float]] = {}   # key → (value, expires_at)

    async def _get_redis(self) -> Optional[aioredis.Redis]:
        """Return a connected Redis client, or None if Redis is unavailable."""
        if self._redis_ok is False:
            return None
        try:
            if self._redis is None:
                self._redis = await aioredis.from_url(
                    settings.redis_url, decode_responses=True, socket_connect_timeout=2
                )
            await self._redis.ping()
            if self._redis_ok is None:
                logger.info("✅ Redis cache connected")
            self._redis_ok = True
            return self._redis
        except Exception as e:
            if self._redis_ok is not False:
                logger.warning(
                    f"Redis not available ({e}). "
                    "Falling back to in-memory cache (cache is process-local and non-persistent)."
                )
            self._redis_ok = False
            self._redis = None
            return None

    def _cache_key(self, query: str, top_k: int) -> str:
        h = hashlib.md5(f"{query}:{top_k}".encode()).hexdigest()
        return f"rag:query:{h}"

    def _mem_get(self, key: str) -> Optional[str]:
        entry = self._mem.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if _time.time() > expires_at:
            del self._mem[key]
            return None
        return value

    def _mem_set(self, key: str, value: str, ttl: int) -> None:
        self._mem[key] = (value, _time.time() + ttl)

    async def get(self, query: str, top_k: int) -> Optional[list]:
        key = self._cache_key(query, top_k)
        r = await self._get_redis()
        try:
            if r is not None:
                value = await r.get(key)
            else:
                value = self._mem_get(key)
            if value:
                return json.loads(value)
        except Exception as e:
            logger.warning(f"Cache get failed: {e}")
        return None

    async def set(self, query: str, top_k: int, results: list) -> None:
        key = self._cache_key(query, top_k)
        serialized = json.dumps(results)
        r = await self._get_redis()
        try:
            if r is not None:
                await r.setex(key, settings.cache_ttl, serialized)
            else:
                self._mem_set(key, serialized, settings.cache_ttl)
        except Exception as e:
            logger.warning(f"Cache set failed: {e}")

    async def ping(self) -> bool:
        r = await self._get_redis()
        if r is not None:
            try:
                return await r.ping()
            except Exception:
                return False
        return False

    async def status(self) -> str:
        if self._redis_ok is False:
            return "fallback"
        r = await self._get_redis()
        if r is None:
            return "fallback"
        try:
            return "healthy" if await r.ping() else "offline"
        except Exception:
            return "offline"


# ══════════════════════════════════════════════════════════════════════════════
# Graph Index — Entity Relationship Graph
# ══════════════════════════════════════════════════════════════════════════════

from collections import defaultdict

class GraphIndex:
    """
    Lightweight in-memory entity co-occurrence graph.
    Nodes  = entities extracted during enrichment (e.g. "ORG:OpenAI")
    Edges  = co-occurrence inside the same chunk → chunk_ids stored on edge
    Used for graph-based retrieval: given query entities, walk the graph to
    find related chunk_ids that share entity neighbours.
    """

    def __init__(self):
        # entity → set of chunk_ids that contain it
        self._entity_chunks: dict[str, set[str]] = defaultdict(set)
        # (entity_a, entity_b) → set of chunk_ids where they co-occur
        self._edges: dict[tuple[str, str], set[str]] = defaultdict(set)

    def add_chunk(self, chunk) -> None:
        """Index a chunk's entities into the graph."""
        entities: list[str] = chunk.metadata.entities or []
        cid = chunk.id
        for ent in entities:
            self._entity_chunks[ent].add(cid)
        # Build co-occurrence edges (undirected)
        for i, a in enumerate(entities):
            for b in entities[i + 1:]:
                key = (min(a, b), max(a, b))
                self._edges[key].add(cid)

    def build(self, chunks: list) -> None:
        """Rebuild the full graph from a list of chunks."""
        self._entity_chunks.clear()
        self._edges.clear()
        for chunk in chunks:
            self.add_chunk(chunk)
        logger.info(f"Graph index built: {len(self._entity_chunks)} entities, "
                    f"{len(self._edges)} edges")

    def query(self, entities: list[str], top_k: int = 10) -> list[str]:
        """
        Return chunk_ids reachable from the given query entities.
        First collects direct entity matches, then expands via edges.
        """
        matched_chunks: dict[str, float] = {}

        # Direct entity hits (score 1.0)
        for ent in entities:
            for cid in self._entity_chunks.get(ent, set()):
                matched_chunks[cid] = matched_chunks.get(cid, 0) + 1.0

        # One-hop neighbours via co-occurrence edges (score 0.5)
        for ent in entities:
            for (a, b), cids in self._edges.items():
                if ent in (a, b):
                    neighbour = b if ent == a else a
                    for cid in self._entity_chunks.get(neighbour, set()):
                        matched_chunks[cid] = matched_chunks.get(cid, 0) + 0.5

        # Sort by score descending
        ranked = sorted(matched_chunks.items(), key=lambda x: x[1], reverse=True)
        return [cid for cid, _ in ranked[:top_k]]

    @property
    def node_count(self) -> int:
        return len(self._entity_chunks)

    @property
    def edge_count(self) -> int:
        return len(self._edges)


# ══════════════════════════════════════════════════════════════════════════════
# Singletons
# ══════════════════════════════════════════════════════════════════════════════

chroma_store = ChromaStore()
bm25_index   = BM25Index()
query_cache  = QueryCache()
graph_index  = GraphIndex()
