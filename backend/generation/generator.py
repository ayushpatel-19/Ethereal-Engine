"""
Ethereal Engine — Generation Pipeline
Phase 04: Generation + Output

Handles:
  • System prompt construction
  • Context assembly with token budget
  • Grounded generation via Ollama (streaming)
  • Citation extraction
  • Confidence scoring
  • Refusal if low confidence
"""
from __future__ import annotations

import time
import re
import json
from typing import AsyncGenerator, Optional

import httpx
from loguru import logger

from core.config import get_settings
from core.models import (
    RetrievedChunk, GenerationResponse, Citation, QueryUnderstanding
)

settings = get_settings()
_ollama_generation_timeout = httpx.Timeout(
    connect=10.0,
    read=max(300.0, float(settings.ollama_timeout)),
    write=60.0,
    pool=60.0,
)
_groq_generation_timeout = httpx.Timeout(
    connect=10.0,
    read=max(60.0, float(settings.groq_timeout)),
    write=30.0,
    pool=30.0,
)


def _resolve_generation_backend(
    model: str | None,
    provider: str | None,
) -> tuple[str, str]:
    selected_provider = (provider or settings.preferred_generation_provider).strip().lower()
    if selected_provider == "groq":
        if not settings.groq_enabled:
            raise RuntimeError(
                "Groq generation is selected, but GROQ_API_KEY is not configured."
            )
        return "groq", model or settings.groq_model
    return "ollama", model or settings.ollama_llm_model


async def _raise_generation_error(response: httpx.Response, provider_name: str) -> None:
    if not response.is_error:
        return

    detail = (await response.aread()).decode(errors="replace").strip()
    if not detail:
        detail = response.reason_phrase
    raise RuntimeError(
        f"{provider_name} chat request failed ({response.status_code}): {detail[:500]}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# System Prompt
# ══════════════════════════════════════════════════════════════════════════════

BASE_SYSTEM_PROMPT = """You are Ethereal Engine, a precise and helpful AI assistant powered by a RAG pipeline.

RULES:
1. Answer ONLY based on the provided context. Do not use outside knowledge.
2. If the context does not contain enough information, say "I don't have enough information in my knowledge base to answer this."
3. Always cite your sources inline using the citation labels provided in context, such as [C1] or [C2].
4. Be concise but complete. Prefer bullet points for lists, prose for explanations.
5. If you are uncertain, say so explicitly with your confidence level.
6. Never fabricate facts, numbers, or citations.

FORMAT:
- Use clear paragraph breaks
- Bold key terms with **term**
- Use numbered steps for procedures
- End with a "Sources:" section listing all cited documents
"""


def build_context_block(chunks: list[RetrievedChunk]) -> str:
    """Format retrieved chunks into a numbered context block."""
    parts = []
    for i, result in enumerate(chunks, 1):
        source = result.chunk.metadata.title or result.chunk.metadata.source
        score  = result.rerank_score or result.score
        parts.append(
            f"[C{i}] Source: {source} (relevance: {score:.2f})\n"
            f"{result.chunk.content}\n"
            f"{'─' * 60}"
        )
    return '\n\n'.join(parts)


def build_prompt(query: str,
                 context: str,
                 chat_history: list[dict],
                 system_prompt: str | None = None) -> list[dict]:
    """Build the message list for Ollama chat API."""
    messages = [{"role": "system", "content": system_prompt or BASE_SYSTEM_PROMPT}]

    # Add chat history (trimmed to last 6 turns)
    for turn in chat_history[-6:]:
        messages.append({"role": turn["role"], "content": turn["content"]})

    # Final user message with context
    user_content = f"""CONTEXT FROM KNOWLEDGE BASE:
{context}

USER QUESTION:
{query}

Answer based strictly on the context above. Cite sources inline using the [C#] labels."""

    messages.append({"role": "user", "content": user_content})
    return messages


# ══════════════════════════════════════════════════════════════════════════════
# Citation Extraction
# ══════════════════════════════════════════════════════════════════════════════

def extract_citations(answer: str, chunks: list[RetrievedChunk]) -> list[Citation]:
    """Match cited [C#] labels in the answer back to retrieved chunks."""
    citations = []
    seen_indices = set()

    for match in re.findall(r"\[C(\d+)\]", answer):
        idx = int(match) - 1
        if idx < 0 or idx >= len(chunks) or idx in seen_indices:
            continue
        seen_indices.add(idx)
        chunk = chunks[idx]
        source = chunk.chunk.metadata.title or chunk.chunk.metadata.source
        excerpt = chunk.chunk.content[:200].strip()
        if len(chunk.chunk.content) > 200:
            excerpt += "..."
        citations.append(Citation(
            chunk_id=chunk.chunk.id,
            source=source,
            excerpt=excerpt,
            relevance_score=chunk.rerank_score or chunk.score,
        ))

    if not citations:
        for chunk in chunks[:3]:
            source = chunk.chunk.metadata.title or chunk.chunk.metadata.source
            excerpt = chunk.chunk.content[:200].strip()
            if len(chunk.chunk.content) > 200:
                excerpt += "..."
            citations.append(Citation(
                chunk_id=chunk.chunk.id,
                source=source,
                excerpt=excerpt,
                relevance_score=chunk.rerank_score or chunk.score,
            ))
            if len(citations) >= 3:
                break

    return citations


def compute_confidence(answer: str, chunks: list[RetrievedChunk]) -> float:
    """Heuristic confidence score based on retrieval scores and answer indicators."""
    if not chunks:
        return 0.1

    # Low confidence signals
    low_signals = ["don't have enough information", "cannot answer", "not sure",
                   "unclear", "i'm not certain", "no information"]
    if any(signal in answer.lower() for signal in low_signals):
        return 0.2

    # Average top-3 rerank scores
    scores = [r.rerank_score or r.score for r in chunks[:3]]
    avg_score = sum(scores) / len(scores) if scores else 0.0

    # Bonus for longer, more detailed answers
    detail_bonus = min(0.1, len(answer) / 5000)

    return min(1.0, avg_score + detail_bonus)


# ══════════════════════════════════════════════════════════════════════════════
# Streaming Generation
# ══════════════════════════════════════════════════════════════════════════════

async def generate_stream(
    query: str,
    chunks: list[RetrievedChunk],
    chat_history: list[dict] | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream tokens from Ollama as they are generated.
    Yields raw text tokens for WebSocket streaming.
    """
    context = build_context_block(chunks)
    messages = build_prompt(query, context, chat_history or [], system_prompt)
    selected_provider, selected_model = _resolve_generation_backend(model, provider)
    temperature = temperature or settings.generation_temperature
    max_tokens  = max_tokens  or settings.generation_max_tokens

    if not chunks:
        yield "I don't have any relevant information in my knowledge base to answer this question."
        return

    logger.info(
        f"Generating response for: {query[:60]}... via {selected_provider}:{selected_model}"
    )
    start = time.time()

    if selected_provider == "groq":
        headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
        async with httpx.AsyncClient(
            timeout=_groq_generation_timeout,
            headers=headers,
        ) as client:
            async with client.stream(
                "POST",
                f"{settings.groq_base_url}/chat/completions",
                json={
                    "model": selected_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                },
            ) as response:
                await _raise_generation_error(response, "Groq")
                async for line in response.aiter_lines():
                    payload = line.strip()
                    if not payload or not payload.startswith("data:"):
                        continue
                    data_str = payload[5:].strip()
                    if data_str == "[DONE]":
                        elapsed = int((time.time() - start) * 1000)
                        logger.info(f"Generation complete in {elapsed}ms")
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = data.get("choices") or []
                    delta = choices[0].get("delta", {}) if choices else {}
                    token = delta.get("content", "")
                    if token:
                        yield token
        return

    async with httpx.AsyncClient(timeout=_ollama_generation_timeout) as client:
        async with client.stream(
            "POST",
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": selected_model,
                "messages": messages,
                "stream": True,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                    "stop": ["</answer>"],
                },
            }
        ) as response:
            await _raise_generation_error(response, "Ollama")
            async for line in response.aiter_lines():
                if line.strip():
                    try:
                        data = json.loads(line)
                        token = data.get("message", {}).get("content", "")
                        if token:
                            yield token
                        if data.get("done"):
                            elapsed = int((time.time() - start) * 1000)
                            logger.info(f"Generation complete in {elapsed}ms")
                            break
                    except json.JSONDecodeError:
                        continue


async def generate(
    query: str,
    chunks: list[RetrievedChunk],
    chat_history: list[dict] | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> GenerationResponse:
    """
    Non-streaming generation. Collects full response and returns structured output.
    """
    start = time.time()
    full_answer = ""
    selected_provider, selected_model = _resolve_generation_backend(model, provider)

    async for token in generate_stream(
        query,
        chunks,
        chat_history,
        system_prompt,
        selected_model,
        selected_provider,
        temperature,
        max_tokens,
    ):
        full_answer += token

    elapsed_ms = int((time.time() - start) * 1000)
    citations  = extract_citations(full_answer, chunks)
    confidence = compute_confidence(full_answer, chunks)

    return GenerationResponse(
        answer=full_answer.strip(),
        citations=citations,
        confidence=confidence,
        latency_ms=elapsed_ms,
        model=selected_model,
        token_usage={
            "estimated_prompt_tokens": sum(c.chunk.token_count for c in chunks),
            "estimated_completion_tokens": len(full_answer.split()),
        },
    )
