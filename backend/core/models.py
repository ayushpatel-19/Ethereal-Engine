"""
Ethereal Engine — Shared Data Models
Pydantic schemas used across all pipeline phases.
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, Any
from enum import Enum
import time


# ══════════════════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════════════════

class SourceType(str, Enum):
    PDF       = "pdf"
    DOCX      = "docx"
    TXT       = "txt"
    URL       = "url"
    API       = "api"

class ChunkStrategy(str, Enum):
    FIXED     = "fixed"
    SEMANTIC  = "semantic"
    OVERLAP   = "overlap"
    PARENT_CHILD = "parent_child"

class PipelineStatus(str, Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    DONE       = "done"
    ERROR      = "error"


# ══════════════════════════════════════════════════════════════════════════════
# Ingestion
# ══════════════════════════════════════════════════════════════════════════════

class IngestURLRequest(BaseModel):
    url: str
    max_depth: int = Field(default=1, ge=1, le=5)
    max_pages: int = Field(default=10, ge=1, le=50)

class IngestAPIRequest(BaseModel):
    endpoint: str
    method: str = "GET"
    headers: dict[str, str] = {}
    body: Optional[dict] = None
    json_path: Optional[str] = None   # JSONPath to extract text e.g. "$.data[*].content"

class DocumentMetadata(BaseModel):
    source: str
    source_type: SourceType
    title: Optional[str] = None
    author: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    page_count: Optional[int] = None
    language: Optional[str] = "en"
    entities: list[str] = []
    keywords: list[str] = []
    summary: Optional[str] = None
    permissions: list[str] = ["public"]

class RawDocument(BaseModel):
    id: str
    content: str
    metadata: DocumentMetadata

class Chunk(BaseModel):
    id: str
    doc_id: str
    content: str
    metadata: DocumentMetadata
    chunk_index: int
    token_count: int
    embedding: Optional[list[float]] = None
    parent_chunk_id: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
# Retrieval
# ══════════════════════════════════════════════════════════════════════════════

class QueryRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    chunk_strategy: ChunkStrategy = ChunkStrategy.SEMANTIC
    filters: dict[str, Any] = {}
    use_reranking: bool = True
    use_graph: bool = False
    chat_history: list[dict[str, str]] = []

class RetrievedChunk(BaseModel):
    chunk: Chunk
    score: float
    retrieval_method: str   # "vector" | "bm25" | "hybrid" | "graph"
    rerank_score: Optional[float] = None

class QueryUnderstanding(BaseModel):
    original_query: str
    rewritten_query: str
    intent: str
    entities: list[str]
    keywords: list[str]
    time_awareness: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
# Generation
# ══════════════════════════════════════════════════════════════════════════════

class GenerationRequest(BaseModel):
    query: str
    context_chunks: list[RetrievedChunk] = []   # optional — routes retrieve internally
    chat_history: list[dict[str, str]] = []
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    temperature: float = 0.1
    max_tokens: int = 1024
    stream: bool = True

class Citation(BaseModel):
    chunk_id: str
    source: str
    excerpt: str
    relevance_score: float

class GenerationResponse(BaseModel):
    answer: str
    citations: list[Citation]
    confidence: float
    reasoning: Optional[str] = None
    latency_ms: int
    model: str
    token_usage: dict[str, int]


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline Events (WebSocket streaming)
# ══════════════════════════════════════════════════════════════════════════════

class PipelineEvent(BaseModel):
    event_type: str   # "stage_start" | "stage_done" | "token" | "error" | "complete"
    stage: str        # "ingestion" | "chunking" | "embedding" | "retrieval" | "generation"
    message: str
    data: Optional[dict] = None
    timestamp: float = Field(default_factory=time.time)


# ══════════════════════════════════════════════════════════════════════════════
# System Stats (Observability)
# ══════════════════════════════════════════════════════════════════════════════

class SystemStats(BaseModel):
    total_documents: int
    total_chunks: int
    total_queries: int
    avg_latency_ms: float
    cache_hit_rate: float
    active_websockets: int
    ollama_status: str
    chroma_status: str
    redis_status: str


# ══════════════════════════════════════════════════════════════════════════════
# Feedback Loop
# ══════════════════════════════════════════════════════════════════════════════

class FeedbackRating(str, Enum):
    THUMBS_UP   = "up"
    THUMBS_DOWN = "down"
    NEUTRAL     = "neutral"

class FeedbackRequest(BaseModel):
    query: str
    answer: str
    rating: FeedbackRating
    comment: Optional[str] = None
    retrieved_chunk_ids: list[str] = []
    session_id: Optional[str] = None

class FeedbackRecord(FeedbackRequest):
    id: str = Field(default_factory=lambda: str(__import__('uuid').uuid4()))
    timestamp: float = Field(default_factory=time.time)


# ══════════════════════════════════════════════════════════════════════════════
# Evaluation
# ══════════════════════════════════════════════════════════════════════════════

class EvalSample(BaseModel):
    question: str
    expected_answer: str
    expected_sources: list[str] = []

class EvalRequest(BaseModel):
    samples: list[EvalSample]
    top_k: int = 5

class EvalResult(BaseModel):
    question: str
    expected: str
    actual: str
    sources_retrieved: list[str]
    expected_sources: list[str]
    source_recall: float          # fraction of expected sources found
    answer_similarity: float      # naive word-overlap similarity
    latency_ms: int
    passed: bool                  # source_recall >= 0.5 AND answer_similarity >= 0.3

class EvalReport(BaseModel):
    total: int
    passed: int
    pass_rate: float
    avg_latency_ms: float
    avg_source_recall: float
    avg_answer_similarity: float
    results: list[EvalResult]


# ══════════════════════════════════════════════════════════════════════════════
# Traces (Observability)
# ══════════════════════════════════════════════════════════════════════════════

class TraceStep(BaseModel):
    stage: str
    duration_ms: int
    detail: Optional[str] = None

class TraceRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(__import__('uuid').uuid4()))
    session_id: Optional[str] = None
    query: str
    steps: list[TraceStep]
    total_ms: int
    chunk_count: int
    timestamp: float = Field(default_factory=time.time)
