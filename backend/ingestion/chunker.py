"""
Ethereal Engine — Chunking Pipeline
Phase 01 (continued): Splitting documents into retrievable chunks.

Strategies:
  • Fixed       — Simple character/token-based splits
  • Overlap     — Fixed with sliding window overlap
  • Semantic    — Split on meaning boundaries (sentence embeddings similarity)
  • Parent-Child — Large parent chunk + small child chunks for multi-level retrieval
"""
from __future__ import annotations

import uuid
import re
from typing import Generator

import tiktoken
from loguru import logger

from core.config import get_settings
from core.models import RawDocument, Chunk, ChunkStrategy

settings = get_settings()
_enc = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def split_into_sentences(text: str) -> list[str]:
    """Naive but fast sentence splitter (no NLTK dependency at runtime)."""
    text = re.sub(r'([.!?])\s+', r'\1\n', text)
    sentences = [s.strip() for s in text.split('\n') if s.strip()]
    return sentences


# ══════════════════════════════════════════════════════════════════════════════
# Fixed Chunking
# ══════════════════════════════════════════════════════════════════════════════

def chunk_fixed(doc: RawDocument, chunk_size: int | None = None) -> list[Chunk]:
    """Split on token boundaries, no overlap."""
    chunk_size = chunk_size or settings.chunk_size
    tokens = _enc.encode(doc.content)
    chunks = []

    for i, start in enumerate(range(0, len(tokens), chunk_size)):
        token_slice = tokens[start:start + chunk_size]
        text = _enc.decode(token_slice)
        chunks.append(Chunk(
            id=str(uuid.uuid4()),
            doc_id=doc.id,
            content=text,
            metadata=doc.metadata,
            chunk_index=i,
            token_count=len(token_slice),
        ))

    logger.debug(f"Fixed chunking: {len(chunks)} chunks from doc {doc.id[:8]}")
    return chunks


# ══════════════════════════════════════════════════════════════════════════════
# Overlap Chunking (Sliding Window)
# ══════════════════════════════════════════════════════════════════════════════

def chunk_overlap(doc: RawDocument,
                  chunk_size: int | None = None,
                  overlap: int | None = None) -> list[Chunk]:
    """Fixed-size chunks with token overlap for context continuity."""
    chunk_size = chunk_size or settings.chunk_size
    overlap    = overlap    or settings.chunk_overlap
    stride     = chunk_size - overlap

    tokens = _enc.encode(doc.content)
    chunks = []
    i = 0

    while i < len(tokens):
        token_slice = tokens[i:i + chunk_size]
        text = _enc.decode(token_slice)
        chunks.append(Chunk(
            id=str(uuid.uuid4()),
            doc_id=doc.id,
            content=text,
            metadata=doc.metadata,
            chunk_index=len(chunks),
            token_count=len(token_slice),
        ))
        if i + chunk_size >= len(tokens):
            break
        i += stride

    logger.debug(f"Overlap chunking: {len(chunks)} chunks (overlap={overlap})")
    return chunks


# ══════════════════════════════════════════════════════════════════════════════
# Semantic Chunking
# ══════════════════════════════════════════════════════════════════════════════

def chunk_semantic(doc: RawDocument,
                   embed_fn=None,
                   threshold: float | None = None,
                   max_chunk_tokens: int | None = None) -> list[Chunk]:
    """
    Group sentences by semantic similarity.
    Falls back to paragraph-based splitting if embed_fn not provided.
    """
    threshold = threshold or settings.semantic_chunk_threshold
    max_tokens = max_chunk_tokens or settings.chunk_size

    sentences = split_into_sentences(doc.content)
    if not sentences:
        return chunk_overlap(doc)

    if embed_fn is None:
        # Fallback: paragraph-based semantic split
        return _paragraph_chunk(doc, max_tokens)

    # Embed all sentences and group by cosine similarity
    import numpy as np

    embeddings = [embed_fn(s) for s in sentences]
    groups = [[0]]

    for i in range(1, len(sentences)):
        e1 = np.array(embeddings[i - 1])
        e2 = np.array(embeddings[i])
        cosine = float(np.dot(e1, e2) / (np.linalg.norm(e1) * np.linalg.norm(e2) + 1e-9))

        if cosine >= threshold and count_tokens(' '.join(sentences[g] for g in groups[-1])) < max_tokens:
            groups[-1].append(i)
        else:
            groups.append([i])

    chunks = []
    for idx, group in enumerate(groups):
        text = ' '.join(sentences[i] for i in group)
        chunks.append(Chunk(
            id=str(uuid.uuid4()),
            doc_id=doc.id,
            content=text,
            metadata=doc.metadata,
            chunk_index=idx,
            token_count=count_tokens(text),
        ))

    logger.debug(f"Semantic chunking: {len(chunks)} chunks")
    return chunks


def _paragraph_chunk(doc: RawDocument, max_tokens: int) -> list[Chunk]:
    """Group paragraphs into chunks respecting max token limit."""
    paragraphs = [p.strip() for p in doc.content.split('\n\n') if p.strip()]
    chunks = []
    current_parts = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = count_tokens(para)
        if current_tokens + para_tokens > max_tokens and current_parts:
            text = '\n\n'.join(current_parts)
            chunks.append(Chunk(
                id=str(uuid.uuid4()),
                doc_id=doc.id,
                content=text,
                metadata=doc.metadata,
                chunk_index=len(chunks),
                token_count=current_tokens,
            ))
            current_parts = [para]
            current_tokens = para_tokens
        else:
            current_parts.append(para)
            current_tokens += para_tokens

    if current_parts:
        text = '\n\n'.join(current_parts)
        chunks.append(Chunk(
            id=str(uuid.uuid4()),
            doc_id=doc.id,
            content=text,
            metadata=doc.metadata,
            chunk_index=len(chunks),
            token_count=current_tokens,
        ))

    return chunks


# ══════════════════════════════════════════════════════════════════════════════
# Parent-Child Chunking
# ══════════════════════════════════════════════════════════════════════════════

def chunk_parent_child(doc: RawDocument,
                       parent_size: int = 1024,
                       child_size: int = 256) -> tuple[list[Chunk], list[Chunk]]:
    """
    Create large parent chunks + small child chunks linked by parent_chunk_id.
    Children are retrieved; parents are used for context assembly.
    """
    parent_doc = RawDocument(id=doc.id, content=doc.content, metadata=doc.metadata)
    parents = chunk_fixed(parent_doc, chunk_size=parent_size)
    all_children = []

    for parent in parents:
        parent_raw = RawDocument(id=doc.id, content=parent.content, metadata=doc.metadata)
        children = chunk_fixed(parent_raw, chunk_size=child_size)
        for child in children:
            child.parent_chunk_id = parent.id
        all_children.extend(children)

    logger.debug(f"Parent-child: {len(parents)} parents, {len(all_children)} children")
    return parents, all_children


# ══════════════════════════════════════════════════════════════════════════════
# Dispatcher
# ══════════════════════════════════════════════════════════════════════════════

def chunk_document(doc: RawDocument,
                   strategy: ChunkStrategy = ChunkStrategy.SEMANTIC,
                   embed_fn=None) -> list[Chunk]:
    """Route to the correct chunking strategy."""
    if strategy == ChunkStrategy.FIXED:
        return chunk_fixed(doc)
    elif strategy == ChunkStrategy.OVERLAP:
        return chunk_overlap(doc)
    elif strategy == ChunkStrategy.SEMANTIC:
        return chunk_semantic(doc, embed_fn=embed_fn)
    elif strategy == ChunkStrategy.PARENT_CHILD:
        parents, children = chunk_parent_child(doc)
        return children   # Index children, store parents separately
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
