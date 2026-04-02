# Ethereal Engine

Local end-to-end RAG system for Windows with:

- React + Vite frontend
- FastAPI backend
- Groq or Ollama for generation
- Ollama or local sentence-transformer embeddings
- ChromaDB vector storage
- BM25 hybrid retrieval
- Optional Redis cache with automatic in-memory fallback

No Docker is required.

## Architecture

The app now maps the pipeline in four stages:

1. Data + Ingestion
   Upload files, crawl URLs, or ingest API responses.
   Documents are parsed, enriched, chunked, and embedded locally.
2. Embedding + Storage
   Chunks are stored in ChromaDB and indexed for BM25 retrieval.
3. Retrieval Pipeline
   Query understanding, hybrid retrieval, deduplication, reranking, and context assembly.
4. Generation + Output
   Groq or Ollama generates grounded answers with citations and feedback capture.

## Local Stack

| Service | Default URL | Notes |
| --- | --- | --- |
| React frontend | `http://localhost:3010` | Vite dev server |
| FastAPI backend | `http://localhost:8010` | REST + WebSocket |
| API docs | `http://localhost:8010/docs` | Swagger |
| Ollama | `http://localhost:11434` | Local models |

## Frontend Routes

The old HTML mockups have been replaced by React routes while keeping the same visual structure:

- `/` - Ingestion
- `/storage` - Storage
- `/retrieval` - Retrieval
- `/generation` - Generation

Legacy `*.html` entry files now redirect into these React routes.

## Windows Quick Start

1. Install Python 3.11+
2. Install Node.js 18+
3. Install Ollama
4. Run [start_windows.bat](C:/Users/91798/Downloads/ethereal-engine-windows/start_windows.bat)

The launcher will:

- create a backend virtual environment if needed
- start Ollama
- pull the local models used by this setup
- install backend and frontend dependencies
- start FastAPI on port 8000
- start FastAPI on port 8010
- start the React frontend on port 3010

## Manual Start

Backend:

```powershell
cd backend
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
python -m uvicorn main:app --host 0.0.0.0 --port 8010 --reload
```

Frontend:

```powershell
cd frontend
npm install
$env:VITE_API_BASE="http://localhost:8010/api"
npm run dev -- --host 0.0.0.0 --port 3010
```

## Default Local Models

This Windows-local setup is aligned to:

```env
OLLAMA_LLM_MODEL=phi3:mini
OLLAMA_EMBED_MODEL=nomic-embed-text
```

You can change them in `backend/.env` after pulling alternative Ollama models.

## Cloud Test Deployment

This repo now includes a free-tier test deployment path:

- Backend on Render using `render.yaml`
- Frontend on Vercel using `frontend/vercel.json`
- Groq for generation
- Local sentence-transformer embeddings in the backend, so Ollama is not required in the cloud

Recommended cloud env vars for the backend:

```env
GENERATION_PROVIDER=groq
EMBEDDING_PROVIDER=local
QUERY_UNDERSTANDING_PROVIDER=rule
CHROMA_PATH=/tmp/chroma_db
UPLOAD_PATH=/tmp/uploads
GROQ_API_KEY=your_groq_key
CORS_ORIGINS=https://your-frontend.vercel.app
```

Important free-tier limitation:

- Render free web services use ephemeral storage, so uploaded files and Chroma data are not durable.
- This is fine for public testing, but not for production.
- If the service restarts or spins down, users may need to re-upload documents.

Frontend deploy notes:

- Deploy the `frontend` folder to Vercel
- Set `VITE_API_BASE=https://your-render-service.onrender.com/api`
- The included `frontend/vercel.json` rewrites all routes to `index.html` so React Router works on refresh

## Backend Features Mapped to the UI

- Ingestion page
  File upload, URL ingestion, API ingestion, live progress, source listing, source deletion.
- Storage page
  Chroma/BM25 stats, source counts, cache status, storage overview.
- Retrieval page
  Query understanding, hybrid retrieval, reranking, result inspection, handoff to generation.
- Generation page
  Local model selection, retrieval-backed generation, citations, confidence, and thumbs up/down feedback.

## Notes

- Redis is optional. If it is unavailable, the app falls back to an in-memory cache.
- FlashRank reranking falls back to a local heuristic when a local rerank model is not already cached.
- Everything runs locally on Windows without Docker.
