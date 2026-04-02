/**
 * Ethereal Engine — API Client
 * Wraps all backend REST endpoints with error handling and loading states.
 */

const API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
  ? 'http://localhost:8010/api'
  : '/api';

const WS_BASE = API_BASE.replace('http', 'ws') + '/ws';

// ─── Session ID for WebSocket routing ─────────────────────────────────────
const SESSION_ID = 'sess_' + Math.random().toString(36).slice(2, 11);

// ─── WebSocket Manager ────────────────────────────────────────────────────
class PipelineSocket {
  constructor() {
    this.ws = null;
    this.listeners = new Map();
    this.reconnectDelay = 2000;
    this._connect();
  }

  _connect() {
    try {
      this.ws = new WebSocket(`${WS_BASE}/${SESSION_ID}`);
      this.ws.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data);
          this._emit(event.event_type, event);
          this._emit('*', event);
        } catch {}
      };
      this.ws.onclose = () => {
        setTimeout(() => this._connect(), this.reconnectDelay);
      };
      this.ws.onerror = () => {};
      // Keep alive
      this._pingInterval = setInterval(() => {
        if (this.ws?.readyState === WebSocket.OPEN) this.ws.send('ping');
      }, 25000);
    } catch (e) {
      console.warn('WebSocket unavailable, using polling mode');
    }
  }

  on(eventType, callback) {
    if (!this.listeners.has(eventType)) this.listeners.set(eventType, []);
    this.listeners.get(eventType).push(callback);
    return () => this.off(eventType, callback);
  }

  off(eventType, callback) {
    const list = this.listeners.get(eventType) || [];
    this.listeners.set(eventType, list.filter(fn => fn !== callback));
  }

  _emit(eventType, data) {
    (this.listeners.get(eventType) || []).forEach(fn => fn(data));
  }
}

// ─── HTTP Helper ──────────────────────────────────────────────────────────
async function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ─── Ingestion API ────────────────────────────────────────────────────────
const Ingestion = {
  async uploadFile(file, chunkStrategy = 'semantic', onProgress) {
    const form = new FormData();
    form.append('file', file);
    form.append('chunk_strategy', chunkStrategy);
    form.append('session_id', SESSION_ID);

    const res = await fetch(`${API_BASE}/ingest/file`, {
      method: 'POST',
      body: form,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Upload failed: ${res.status}`);
    }
    return res.json();
  },

  async ingestURL(url, maxDepth = 1, maxPages = 10) {
    return apiFetch(`/ingest/url?session_id=${SESSION_ID}`, {
      method: 'POST',
      body: JSON.stringify({ url, max_depth: maxDepth, max_pages: maxPages }),
    });
  },

  async ingestAPI(endpoint, method = 'GET', headers = {}, body = null, jsonPath = null) {
    return apiFetch(`/ingest/api?session_id=${SESSION_ID}`, {
      method: 'POST',
      body: JSON.stringify({ endpoint, method, headers, body, json_path: jsonPath }),
    });
  },

  async listSources() {
    return apiFetch('/ingest/sources');
  },

  async deleteSource(source) {
    return apiFetch(`/ingest/source?source=${encodeURIComponent(source)}`, { method: 'DELETE' });
  },
};

// ─── Retrieval API ────────────────────────────────────────────────────────
const Retrieval = {
  async query(query, topK = 5, filters = {}, useReranking = true) {
    return apiFetch('/retrieve', {
      method: 'POST',
      body: JSON.stringify({ query, top_k: topK, filters, use_reranking: useReranking }),
    });
  },
};

// ─── Generation API ───────────────────────────────────────────────────────
const Generation = {
  async generate(query, chatHistory = []) {
    return apiFetch('/generate', {
      method: 'POST',
      body: JSON.stringify({ query, chat_history: chatHistory, stream: false }),
    });
  },

  async *streamGenerate(query, chatHistory = []) {
    const res = await fetch(`${API_BASE}/generate/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, chat_history: chatHistory, stream: true }),
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data === '[DONE]') return;
          try {
            const parsed = JSON.parse(data);
            if (parsed.token) yield parsed.token;
          } catch {}
        }
      }
    }
  },
};

// ─── Feedback API ─────────────────────────────────────────────────────────
const Feedback = {
  async submit(query, answer, rating, comment = '', chunkIds = []) {
    return apiFetch('/feedback', {
      method: 'POST',
      body: JSON.stringify({
        query, answer, rating, comment,
        retrieved_chunk_ids: chunkIds,
        session_id: SESSION_ID,
      }),
    });
  },
  async list(limit = 50, rating = null) {
    const qs = rating ? `?limit=${limit}&rating=${rating}` : `?limit=${limit}`;
    return apiFetch(`/feedback${qs}`);
  },
  async clear() {
    return apiFetch('/feedback', { method: 'DELETE' });
  },
};

// ─── Evaluation API ───────────────────────────────────────────────────────
const Evaluation = {
  async run(samples, topK = 5) {
    return apiFetch('/eval', {
      method: 'POST',
      body: JSON.stringify({ samples, top_k: topK }),
    });
  },
};

// ─── Traces API ───────────────────────────────────────────────────────────
const Traces = {
  async list(limit = 50) {
    return apiFetch(`/traces?limit=${limit}`);
  },
  async clear() {
    return apiFetch('/traces', { method: 'DELETE' });
  },
};

// ─── System API ───────────────────────────────────────────────────────────
const System = {
  async health()   { return apiFetch('/health'); },
  async stats()    { return apiFetch('/stats'); },
  async models()   { return apiFetch('/models'); },
  async progress() { return apiFetch('/ingest/progress'); },
};

// ─── Global socket instance ────────────────────────────────────────────────
window.EtherealAPI = { Ingestion, Retrieval, Generation, Feedback, Evaluation, Traces, System, SESSION_ID };
window.PipelineSocket = new PipelineSocket();

// ─── UI Helpers ───────────────────────────────────────────────────────────
function showToast(message, type = 'info') {
  const colors = {
    info:    'border-blue-400 text-blue-300',
    success: 'border-green-400 text-green-300',
    error:   'border-red-400 text-red-300',
    warning: 'border-yellow-400 text-yellow-300',
  };
  const toast = document.createElement('div');
  toast.className = `fixed bottom-24 right-8 z-50 bg-slate-900/90 backdrop-blur border-l-2 ${colors[type]} px-6 py-4 rounded-md font-mono text-xs max-w-sm shadow-2xl transition-all duration-300 translate-y-4 opacity-0`;
  toast.textContent = message;
  document.body.appendChild(toast);
  requestAnimationFrame(() => {
    toast.classList.remove('translate-y-4', 'opacity-0');
  });
  setTimeout(() => {
    toast.classList.add('translate-y-4', 'opacity-0');
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

function formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function animateCount(element, from, to, duration = 800) {
  const start = Date.now();
  const tick = () => {
    const elapsed = Date.now() - start;
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    element.textContent = Math.round(from + (to - from) * eased).toLocaleString();
    if (progress < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

// Nav routing
document.addEventListener('DOMContentLoaded', () => {
  // Highlight current page in nav
  const page = window.location.pathname.split('/').pop() || 'ingestion.html';
  document.querySelectorAll('[data-nav]').forEach(link => {
    if (link.dataset.nav === page) {
      link.classList.add('text-blue-200', 'bg-blue-500/10', 'border-l-4', 'border-blue-400');
      link.classList.remove('text-slate-500');
    }
  });

  // Load system stats into header badge if present
  const statsEl = document.getElementById('system-chunk-count');
  if (statsEl) {
    System.stats().then(s => {
      animateCount(statsEl, 0, s.total_chunks);
    }).catch(() => {});
  }
});
