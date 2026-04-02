# Windows Setup

This project now runs locally on Windows without Docker.

## Prerequisites

- Python 3.11+
- Node.js 18+
- Ollama

Optional:

- Tesseract OCR for scanned PDFs
- Poppler for PDF image rendering
- Redis or Memurai for persistent query caching

## One-Click Start

Run [start_windows.bat](C:/Users/91798/Downloads/ethereal-engine-windows/start_windows.bat).

It will:

1. create `backend\venv` if it does not exist
2. start Ollama if needed
3. pull `phi3:mini` and `nomic-embed-text`
4. install backend dependencies
5. install frontend dependencies
6. launch FastAPI on `http://localhost:8010`
7. launch the React frontend on `http://localhost:3010`

## Manual Start

### Backend

```powershell
cd backend
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
python -m uvicorn main:app --host 0.0.0.0 --port 8010 --reload
```

### Frontend

```powershell
cd frontend
npm install
$env:VITE_API_BASE="http://localhost:8010/api"
npm run dev -- --host 0.0.0.0 --port 3010
```

## App URLs

- Frontend: `http://localhost:3010`
- Backend: `http://localhost:8010`
- Ingestion: `http://localhost:3000/`
- Storage: `http://localhost:3000/storage`
- Retrieval: `http://localhost:3000/retrieval`
- Generation: `http://localhost:3000/generation`
- API docs: `http://localhost:8010/docs`

## Models

The local Windows configuration currently expects:

```env
OLLAMA_LLM_MODEL=phi3:mini
OLLAMA_EMBED_MODEL=nomic-embed-text
```

Pull alternatives with:

```powershell
ollama pull llama3.2
ollama pull nomic-embed-text
```

Then update `backend/.env` if you want to switch models.

## Storage and Cache

- Vector store: `backend\chroma_db\`
- Uploads: `backend\uploads\`
- Redis: optional
- If Redis is unavailable, the backend uses an in-memory cache automatically

## Troubleshooting

Ollama not responding:

```powershell
ollama serve
```

Frontend dependencies missing:

```powershell
cd frontend
npm install
```

Backend dependencies missing:

```powershell
cd backend
.\venv\Scripts\activate
pip install -r requirements.txt
```

Slow generation on CPU:

- keep using `phi3:mini`, or switch to another small Ollama model

No retrieval results:

- ingest at least one document first from the Ingestion page
