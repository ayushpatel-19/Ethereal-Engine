import { useEffect, useRef } from "react";

import { useGenerationSession } from "../components/GenerationProvider";
import PageShell from "../components/PageShell";

const SUGGESTIONS = [
  "What is this document about?",
  "Summarize the key findings",
  "What are the main topics?",
  "List the most important facts"
];

const EMPTY_CONTEXT = "Retrieved context will appear here...";
const EMPTY_CITATIONS = "Citations will appear after generation...";

function safeParse(value, fallback) {
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function formatAnswer(text) {
  const escaped = escapeHtml(text);
  return escaped
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n\n/g, "</p><p>")
    .replace(/\n/g, "<br />")
    .replace(/^/, "<p>")
    .replace(/$/, "</p>")
    .replace(/<p><\/p>/g, "");
}

function confidenceClass(confidence) {
  if (confidence == null) return "text-slate-400";
  if (confidence >= 0.7) return "text-green-400";
  if (confidence >= 0.4) return "text-yellow-400";
  return "text-red-400";
}

function formatConfidence(confidence) {
  return confidence == null ? "-" : `${Math.round(confidence * 100)}%`;
}

function formatModelLabel(model, providers) {
  const provider = providers?.[model];
  if (provider === "groq") return `Groq - ${model}`;
  if (provider === "ollama") return `Ollama - ${model}`;
  return model;
}

function estimateTokens(chunks) {
  return chunks.reduce((sum, chunk) => {
    const direct = Number(chunk?.token_count || 0);
    const nested = Number(chunk?.chunk?.token_count || 0);
    return sum + direct + nested;
  }, 0);
}

export default function GenerationPage() {
  const timersRef = useRef([]);
  const scrollerRef = useRef(null);
  const prefillHandledRef = useRef(false);
  const {
    query,
    setQuery,
    messages,
    models,
    modelProviders,
    selectedModel,
    setSelectedModel,
    isGenerating,
    latestContext,
    setLatestContext,
    latestCitations,
    confidence,
    sessionStats,
    systemStats,
    clearChat,
    sendMessage,
    submitFeedback
  } = useGenerationSession();

  useEffect(() => {
    return () => {
      timersRef.current.forEach((timer) => window.clearTimeout(timer));
      timersRef.current = [];
    };
  }, []);

  useEffect(() => {
    if (scrollerRef.current) {
      scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    if (prefillHandledRef.current) return;
    const storedQuery = sessionStorage.getItem("rag_query");
    if (!storedQuery) return;

    prefillHandledRef.current = true;
    const storedResults = safeParse(sessionStorage.getItem("rag_context") || "[]", []);
    const storedContext = safeParse(sessionStorage.getItem("rag_context_payload") || "[]", []);

    sessionStorage.removeItem("rag_query");
    sessionStorage.removeItem("rag_context");
    sessionStorage.removeItem("rag_context_payload");

    setQuery(storedQuery);
    if (storedResults.length) {
      setLatestContext(storedResults);
    }

    const timer = window.setTimeout(() => {
      void sendMessage(storedQuery, {
        results: storedResults,
        contextChunks: storedContext
      });
    }, 250);
    timersRef.current.push(timer);
  }, []);

  const sidebarExtra = (
    <div>
      <label className="mb-1 block font-headline text-[9px] uppercase tracking-wider text-slate-500">
        Active Model
      </label>
      <select
        className="w-full rounded border border-outline-variant/20 bg-surface-container-highest px-2 py-2 font-mono text-xs text-primary focus:outline-none focus:ring-1 focus:ring-primary"
        onChange={(event) => setSelectedModel(event.target.value)}
        value={selectedModel}
      >
        {!models.length ? <option value="">No chat models available</option> : null}
        {models.map((model) => (
          <option key={model} value={model}>
            {formatModelLabel(model, modelProviders)}
          </option>
        ))}
      </select>
    </div>
  );

  const sidebarFooter = (
    <div className="space-y-4">
      <div className="border-t border-slate-800 pt-4">
        <button
          className="flex w-full items-center gap-2 px-3 py-2 font-headline text-xs uppercase tracking-wider text-slate-500 transition-colors hover:text-slate-300"
          onClick={clearChat}
          type="button"
        >
          <span className="material-symbols-outlined text-sm">delete_sweep</span>
          Clear Chat
        </button>
        <div className="space-y-1 px-2 py-2 font-mono text-xs text-slate-500">
          <div>
            Queries: <span className="text-primary">{sessionStats.queries}</span>
          </div>
          <div>
            Avg latency: <span className="text-secondary">{sessionStats.avgLatency}</span>
          </div>
        </div>
      </div>
    </div>
  );

  const topbar = (
    <>
      <div className="flex items-center gap-8">
        <span className="font-headline text-xs font-black uppercase tracking-widest text-blue-300">
          SYSTEM DIAGNOSTICS
        </span>
        <nav className="flex gap-6">
          <span className="font-headline text-xs uppercase tracking-widest text-slate-400">
            Cluster Status
          </span>
          <span className="font-headline text-xs uppercase tracking-widest text-slate-400">
            Latency
          </span>
          <span className="border-b border-blue-400 py-1 font-headline text-xs uppercase tracking-widest text-blue-300">
            Tokens
          </span>
        </nav>
      </div>
      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2 rounded-full bg-surface-container-low px-3 py-1">
          <span className={`h-2 w-2 rounded-full ${isGenerating ? "animate-pulse bg-primary" : "bg-green-400"}`} />
          <span className={`font-headline text-[10px] uppercase ${isGenerating ? "text-primary" : "text-green-300"}`}>
            {isGenerating ? "Generating" : "Ready"}
          </span>
        </div>
        <span className="font-mono text-[10px] text-slate-400">{selectedModel || "No model"}</span>
      </div>
    </>
  );

  return (
    <PageShell
      mainClassName="overflow-hidden bg-background"
      sidebarExtra={sidebarExtra}
      sidebarFooter={sidebarFooter}
      topbar={topbar}
    >
      <div className="flex h-[calc(100vh-4rem)] flex-col overflow-hidden">
        <div className="flex shrink-0 items-center justify-between border-b border-outline-variant/10 bg-surface-container-low px-12 py-6">
          <div>
            <span className="font-mono text-xs uppercase tracking-widest text-primary">Phase 04 // Output Synthesis</span>
            <h2 className="mt-1 font-headline text-3xl font-bold tracking-tight text-on-surface">Generation + Output</h2>
          </div>
          <div className="flex gap-4">
            <div className="flex flex-col items-end rounded-sm border-l-2 border-primary bg-surface-container px-4 py-2">
              <span className="font-headline text-[9px] uppercase text-slate-500">Context Window</span>
              <span className="font-mono text-sm text-primary">{estimateTokens(latestContext)} / 4096 tokens</span>
            </div>
            <div className="flex flex-col items-end rounded-sm border-l-2 border-green-500 bg-surface-container px-4 py-2">
              <span className="font-headline text-[9px] uppercase text-slate-500">Confidence</span>
              <span className={`font-mono text-sm ${confidenceClass(confidence)}`}>{formatConfidence(confidence)}</span>
            </div>
          </div>
        </div>

        <div className="flex min-h-0 flex-1">
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="flex-1 overflow-y-auto px-12 py-8" ref={scrollerRef}>
              {!messages.length ? (
                <div className="flex h-full items-center justify-center">
                  <div className="max-w-lg text-center">
                    <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full border border-primary/20 bg-primary/10">
                      <span className="material-symbols-outlined text-2xl text-primary">psychology</span>
                    </div>
                    <h3 className="mb-2 font-headline text-xl font-bold text-on-surface">Ethereal Engine - RAG Chat</h3>
                    <p className="text-sm leading-relaxed text-slate-500">
                      Ask questions about your ingested documents. The engine will retrieve context and generate a grounded answer with citations.
                    </p>
                    <div className="mt-4 flex flex-wrap justify-center gap-2">
                      {SUGGESTIONS.map((suggestion) => (
                        <button
                          className="rounded-full border border-outline-variant/20 bg-surface-container-low px-3 py-1.5 font-headline text-[10px] text-slate-400 transition-all hover:border-primary/30 hover:text-primary"
                          key={suggestion}
                          onClick={() => void sendMessage(suggestion)}
                          type="button"
                        >
                          {suggestion}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="space-y-6">
                  {messages.map((message) => (
                    <div className={`msg-card flex gap-4 ${message.role === "user" ? "justify-end" : "justify-start"}`} key={message.id}>
                      {message.role === "assistant" ? (
                        <>
                          <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-primary/20 bg-primary/10">
                            <span className="material-symbols-outlined text-sm text-primary">psychology</span>
                          </div>
                          <div className="max-w-[80%]">
                            <div className={`rounded-xl rounded-tl-sm bg-surface-container-low px-5 py-4 text-sm leading-relaxed text-on-surface ${message.isStreaming ? "gen-active" : ""}`}>
                              {message.isLoading ? (
                                <div className="flex items-center gap-2">
                                  <div className="h-2 w-2 rounded-full bg-primary thinking-dot" />
                                  <div className="h-2 w-2 rounded-full bg-primary thinking-dot" style={{ animationDelay: ".2s" }} />
                                  <div className="h-2 w-2 rounded-full bg-primary thinking-dot" style={{ animationDelay: ".4s" }} />
                                  <span className="ml-2 font-mono text-xs text-slate-500">Retrieving context...</span>
                                </div>
                              ) : (
                                <div
                                  className="prose-answer"
                                  dangerouslySetInnerHTML={{
                                    __html: `${formatAnswer(message.content)}${message.isStreaming ? '<span class="cursor"></span>' : ""}`
                                  }}
                                />
                              )}
                            </div>
                            {message.latency ? (
                              <div className="mt-1.5 font-mono text-[9px] text-slate-600">
                {message.latency}ms - {formatModelLabel(message.model || selectedModel || "model", modelProviders)} - confidence: {formatConfidence(message.confidence)}
                              </div>
                            ) : null}
                            {!message.isLoading && !message.isStreaming ? (
                              <div className="mt-2 flex items-center gap-2">
                                <span className="font-mono text-[9px] text-slate-600">Was this helpful?</span>
                                <button
                                  className={`rounded border px-1.5 py-0.5 text-xs transition-colors ${message.feedback === "up" ? "border-green-500 text-green-400" : "border-slate-700 text-slate-500 hover:border-green-500 hover:text-green-400"}`}
                                  onClick={() => void submitFeedback(message.id, "up")}
                                  type="button"
                                >
                                  Up
                                </button>
                                <button
                                  className={`rounded border px-1.5 py-0.5 text-xs transition-colors ${message.feedback === "down" ? "border-red-500 text-red-400" : "border-slate-700 text-slate-500 hover:border-red-500 hover:text-red-400"}`}
                                  onClick={() => void submitFeedback(message.id, "down")}
                                  type="button"
                                >
                                  Down
                                </button>
                              </div>
                            ) : null}
                          </div>
                        </>
                      ) : (
                        <>
                          <div className="max-w-[80%] rounded-xl rounded-tr-sm border border-primary/20 bg-primary/10 px-5 py-4 text-sm leading-relaxed">
                            {message.content}
                          </div>
                          <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-container-highest">
                            <span className="material-symbols-outlined text-sm text-slate-400">person</span>
                          </div>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="shrink-0 border-t border-outline-variant/10 bg-background px-12 py-6">
              <div className="relative rounded-xl border border-outline-variant/20 bg-surface-container-low transition-all focus-within:border-primary/40">
                <textarea
                  className="w-full resize-none bg-transparent px-5 py-4 pr-20 font-body text-sm leading-relaxed text-on-surface placeholder:text-slate-600 focus:outline-none"
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && event.ctrlKey) {
                      event.preventDefault();
                      void sendMessage();
                    }
                  }}
                  placeholder="Ask a question about your documents... (Ctrl+Enter to send)"
                  rows="2"
                  value={query}
                />
                <button
                  className="absolute bottom-4 right-4 flex h-10 w-10 items-center justify-center rounded-lg border border-primary/30 bg-primary/20 text-primary transition-all active:scale-95 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={isGenerating}
                  onClick={() => void sendMessage()}
                  type="button"
                >
                  <span className="material-symbols-outlined text-sm">send</span>
                </button>
              </div>
              <div className="mt-2 flex items-center justify-between">
                <span className="font-mono text-[10px] text-slate-600">Grounded | Citations | local/Groq/Ollama cloud-test ready</span>
                <span className="font-mono text-[10px] text-slate-600">Ctrl+Enter to send</span>
              </div>
            </div>
          </div>

          <div className="flex w-80 shrink-0 flex-col overflow-y-auto border-l border-outline-variant/10 bg-surface-container-low">
            <div className="border-b border-outline-variant/10 p-5">
              <h4 className="mb-4 flex items-center gap-2 font-headline text-[10px] uppercase tracking-widest text-slate-500">
                <span className="material-symbols-outlined text-sm text-tertiary">layers</span>
                Context Chunks
              </h4>
              <div className="space-y-2">
                {latestContext.length ? latestContext.slice(0, 5).map((chunk, index) => {
                  const label = chunk.title || chunk.source || `Chunk ${index + 1}`;
                  const score = Math.round(((chunk.rerank_score || chunk.score || 0) * 100));
                  return (
                    <div className="rounded-md bg-surface-container p-3" key={chunk.chunk_id || label}>
                      <div className="mb-1 flex justify-between gap-3">
                        <span className="max-w-[140px] truncate font-mono text-[9px] text-primary" title={label}>{label}</span>
                        <span className="font-mono text-[9px] text-tertiary">{score}%</span>
                      </div>
                      <p className="line-clamp-2 text-[10px] leading-relaxed text-slate-400">{chunk.content || ""}</p>
                    </div>
                  );
                }) : <p className="text-[10px] italic text-slate-600">{EMPTY_CONTEXT}</p>}
              </div>
            </div>

            <div className="border-b border-outline-variant/10 p-5">
              <h4 className="mb-4 flex items-center gap-2 font-headline text-[10px] uppercase tracking-widest text-slate-500">
                <span className="material-symbols-outlined text-sm text-primary">format_quote</span>
                Citations
              </h4>
              <div className="space-y-2">
                {latestCitations.length ? latestCitations.map((citation) => (
                  <div className="rounded-md border-l-2 border-primary/40 bg-surface-container p-3" key={citation.chunk_id}>
                    <div className="mb-1 truncate font-mono text-[9px] text-primary" title={citation.source}>{citation.source}</div>
                    <p className="line-clamp-2 text-[10px] italic text-slate-400">{citation.excerpt}</p>
                    <div className="mt-1 font-mono text-[9px] text-slate-600">
                      relevance: {Math.round((citation.relevance_score || 0) * 100)}%
                    </div>
                  </div>
                )) : <p className="text-[10px] italic text-slate-600">{EMPTY_CITATIONS}</p>}
              </div>
            </div>

            <div className="p-5">
              <h4 className="mb-4 font-headline text-[10px] uppercase tracking-widest text-slate-500">Pipeline Stats</h4>
              <div className="space-y-2 font-mono text-[10px]">
                <div className="flex justify-between">
                  <span className="text-slate-500">Session queries</span>
                  <span className="text-primary">{sessionStats.queries}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Avg latency</span>
                  <span className="text-secondary">{sessionStats.avgLatency}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Cache hits</span>
                  <span className="text-tertiary">{systemStats.cacheHitRate}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Retrieval backend</span>
                  <span className={systemStats.ollamaStatus === "healthy" ? "text-green-400" : "text-slate-400"}>
                    {systemStats.ollamaStatus}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Total queries</span>
                  <span className="text-slate-400">{systemStats.totalQueries}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </PageShell>
  );
}
