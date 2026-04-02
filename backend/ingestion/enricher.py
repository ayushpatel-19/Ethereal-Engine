"""
Ethereal Engine — Enrichment Pipeline
Phase 01 (continued): Enriching chunks with metadata before storage.

Adds:
  • Keyword extraction (TF-IDF style)
  • Entity detection (regex-based NER)
  • Auto-summary via Ollama
  • Language detection
  • Timestamps + permissions
"""
from __future__ import annotations

import re
import time
import math
from collections import Counter
from typing import Optional

import httpx
from loguru import logger

from core.config import get_settings
from core.models import Chunk, RawDocument

settings = get_settings()

# Common English stopwords (inline to avoid NLTK dependency at init)
STOPWORDS = {
    'a','an','the','and','or','but','in','on','at','to','for','of','with',
    'by','from','as','is','was','are','were','be','been','have','has','had',
    'do','does','did','will','would','could','should','may','might','shall',
    'this','that','these','those','it','its','i','we','you','he','she','they',
    'not','no','nor','so','yet','both','either','neither','each','than','then',
    'more','most','other','such','what','which','who','whom','when','where',
    'how','all','any','few','also','just','very','still','only','even',
}

# Simple regex-based entity patterns
ENTITY_PATTERNS = [
    (re.compile(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b'), "PERSON"),
    (re.compile(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b'), "DATE"),
    (re.compile(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*(?:million|billion|trillion|USD|EUR|GBP|%))?\b'), "NUMBER"),
    (re.compile(r'\bhttps?://[^\s<>"]+'), "URL"),
    (re.compile(r'\b[A-Z]{2,}\b'), "ACRONYM"),
]


def extract_keywords(text: str, top_n: int = 10) -> list[str]:
    """Simple TF-IDF-inspired keyword extraction."""
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
    words = [w for w in words if w not in STOPWORDS]

    if not words:
        return []

    # Term frequency
    tf = Counter(words)
    total = len(words)

    # Score by frequency × length bonus (longer words often more meaningful)
    scored = {w: (count / total) * math.log(1 + len(w)) for w, count in tf.items()}
    top = sorted(scored, key=scored.get, reverse=True)[:top_n]
    return top


def extract_entities(text: str) -> list[str]:
    """Extract named entities using regex patterns."""
    entities = []
    for pattern, label in ENTITY_PATTERNS:
        matches = pattern.findall(text)
        for match in matches[:5]:  # Limit per category
            entity_str = f"{label}:{match.strip()}"
            if entity_str not in entities:
                entities.append(entity_str)
    return entities[:20]  # Cap total


def detect_language(text: str) -> str:
    """Very simple language detection based on common word frequency."""
    sample = text[:500].lower()
    english_markers = ['the', 'and', 'is', 'in', 'it', 'of', 'to', 'a']
    count = sum(f' {w} ' in sample for w in english_markers)
    return 'en' if count >= 3 else 'unknown'


async def generate_summary(text: str, max_words: int = 60) -> Optional[str]:
    """Generate a concise summary using Ollama."""
    if len(text) < 200:
        return None  # Too short to summarize

    prompt = (
        f"Summarize the following text in {max_words} words or fewer. "
        f"Be concise and factual. Return only the summary, no preamble.\n\n"
        f"TEXT:\n{text[:2000]}"  # Cap input to avoid timeouts
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.ollama_llm_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 150},
                }
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "").strip()
    except Exception as e:
        logger.warning(f"Summary generation failed: {e}")
        return None


async def enrich_chunk(chunk: Chunk, generate_summaries: bool = False) -> Chunk:
    """Enrich a single chunk with metadata."""
    chunk.metadata.keywords = extract_keywords(chunk.content)
    chunk.metadata.entities = extract_entities(chunk.content)
    chunk.metadata.language = detect_language(chunk.content)
    chunk.metadata.created_at = time.time()

    if generate_summaries and chunk.token_count > 100:
        chunk.metadata.summary = await generate_summary(chunk.content)

    return chunk


async def enrich_document(doc: RawDocument) -> RawDocument:
    """Enrich raw document metadata."""
    doc.metadata.keywords = extract_keywords(doc.content, top_n=15)
    doc.metadata.entities = extract_entities(doc.content)
    doc.metadata.language = detect_language(doc.content)
    return doc
