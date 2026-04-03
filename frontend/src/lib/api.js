const API_BASE =
  import.meta.env.VITE_API_BASE ||
  (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
    ? "http://localhost:8010/api"
    : "/api");

const WS_BASE = API_BASE.replace("http", "ws") + "/ws";

export const SESSION_ID = `sess_${Math.random().toString(36).slice(2, 11)}`;

class PipelineSocketManager {
  constructor() {
    this.ws = null;
    this.listeners = new Map();
    this.reconnectDelay = 2000;
    this.pingInterval = null;
    this.connect();
  }

  connect() {
    try {
      this.ws = new WebSocket(`${WS_BASE}/${SESSION_ID}`);
      this.ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          this.emit(payload.event_type, payload);
          this.emit("*", payload);
        } catch {
          // Ignore malformed socket payloads.
        }
      };
      this.ws.onclose = () => {
        this.stopPing();
        window.setTimeout(() => this.connect(), this.reconnectDelay);
      };
      this.ws.onerror = () => {};
      this.startPing();
    } catch {
      this.stopPing();
    }
  }

  startPing() {
    this.stopPing();
    this.pingInterval = window.setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send("ping");
      }
    }, 25000);
  }

  stopPing() {
    if (this.pingInterval) {
      window.clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  subscribe(eventType, handler) {
    if (!this.listeners.has(eventType)) {
      this.listeners.set(eventType, new Set());
    }
    this.listeners.get(eventType).add(handler);
    return () => {
      const handlers = this.listeners.get(eventType);
      if (handlers) {
        handlers.delete(handler);
      }
    };
  }

  emit(eventType, payload) {
    const handlers = this.listeners.get(eventType);
    if (!handlers) {
      return;
    }
    handlers.forEach((handler) => handler(payload));
  }

  isOpen() {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const isFormData = options.body instanceof FormData;
  const hasBody = options.body != null;

  if (!isFormData && hasBody && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

export const pipelineSocket = new PipelineSocketManager();

export const api = {
  ingestion: {
    async uploadFile(file, chunkStrategy = "semantic") {
      const form = new FormData();
      form.append("file", file);
      form.append("chunk_strategy", chunkStrategy);
      form.append("session_id", SESSION_ID);

      const response = await fetch(`${API_BASE}/ingest/file`, {
        method: "POST",
        body: form
      });

      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || `Upload failed: ${response.status}`);
      }

      return response.json();
    },
    ingestURL(url, maxDepth = 1, maxPages = 10) {
      return apiFetch(`/ingest/url?session_id=${SESSION_ID}`, {
        method: "POST",
        body: JSON.stringify({ url, max_depth: maxDepth, max_pages: maxPages })
      });
    },
    ingestAPI(endpoint, method = "GET", headers = {}, body = null, jsonPath = null) {
      return apiFetch(`/ingest/api?session_id=${SESSION_ID}`, {
        method: "POST",
        body: JSON.stringify({
          endpoint,
          method,
          headers,
          body,
          json_path: jsonPath
        })
      });
    },
    listSources() {
      return apiFetch("/ingest/sources");
    },
    deleteSource(source) {
      return apiFetch(`/ingest/source?source=${encodeURIComponent(source)}`, {
        method: "DELETE"
      });
    }
  },
  retrieval: {
    query({
      query,
      topK = 5,
      filters = {},
      useReranking = true,
      useGraph = false,
      chatHistory = []
    }) {
      return apiFetch("/retrieve", {
        method: "POST",
        body: JSON.stringify({
          query,
          top_k: topK,
          filters,
          use_reranking: useReranking,
          use_graph: useGraph,
          chat_history: chatHistory
        })
      });
    }
  },
  generation: {
    generate({
      query,
      contextChunks = [],
      chatHistory = [],
      systemPrompt = null,
      temperature = 0.1,
      maxTokens = 1024,
      model = null
    }) {
      return apiFetch("/generate", {
        method: "POST",
        body: JSON.stringify({
          query,
          context_chunks: contextChunks,
          chat_history: chatHistory,
          system_prompt: systemPrompt,
          temperature,
          max_tokens: maxTokens,
          model,
          stream: false
        })
      });
    },
    async stream({
      query,
      contextChunks = [],
      chatHistory = [],
      systemPrompt = null,
      temperature = 0.1,
      maxTokens = 1024,
      model = null,
      onToken = null,
      onMeta = null
    }) {
      const response = await fetch(`${API_BASE}/generate/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          query,
          context_chunks: contextChunks,
          chat_history: chatHistory,
          system_prompt: systemPrompt,
          temperature,
          max_tokens: maxTokens,
          model,
          stream: true
        })
      });

      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || `HTTP ${response.status}`);
      }

      if (!response.body) {
        throw new Error("Streaming response body is unavailable.");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finalMeta = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";

        for (const event of events) {
          const dataLine = event
            .split("\n")
            .find((line) => line.startsWith("data:"));

          if (!dataLine) continue;

          const payload = dataLine.slice(5).trim();
          if (!payload) continue;
          if (payload === "[DONE]") {
            return finalMeta;
          }

          const parsed = JSON.parse(payload);
          if (parsed.error) {
            throw new Error(parsed.error);
          }
          if (parsed.token) {
            onToken?.(parsed.token);
          }
          if (parsed.done && parsed.meta) {
            finalMeta = parsed.meta;
            onMeta?.(parsed.meta);
          }
        }
      }

      return finalMeta;
    }
  },
  feedback: {
    submit(query, answer, rating, comment = "", chunkIds = []) {
      return apiFetch("/feedback", {
        method: "POST",
        body: JSON.stringify({
          query,
          answer,
          rating,
          comment,
          retrieved_chunk_ids: chunkIds,
          session_id: SESSION_ID
        })
      });
    }
  },
  traces: {
    list(limit = 50) {
      return apiFetch(`/traces?limit=${limit}`);
    }
  },
  system: {
    health() {
      return apiFetch("/health");
    },
    stats() {
      return apiFetch("/stats");
    },
    models() {
      return apiFetch("/models");
    },
    progress() {
      return apiFetch("/ingest/progress");
    }
  }
};
