import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import PageShell from "../components/PageShell";
import { useToast } from "../components/ToastProvider";
import { api } from "../lib/api";

const badgeClass = (method) => ({
  vector: "badge-vector",
  bm25: "badge-bm25",
  hybrid: "badge-hybrid",
  stitched: "badge-stitched",
  graph: "badge-graph"
}[method] || "badge-vector");

export default function RetrievalPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(5);
  const [rerank, setRerank] = useState(true);
  const [loading, setLoading] = useState(false);
  const [understanding, setUnderstanding] = useState(null);
  const [results, setResults] = useState([]);
  const [contextChunks, setContextChunks] = useState([]);
  const [latency, setLatency] = useState("—");

  const run = async () => {
    if (!query.trim()) return toast("Enter a query first", "warning");
    setLoading(true);
    try {
      const res = await api.retrieval.query({ query, topK: Number(topK), useReranking: rerank });
      setUnderstanding(res.understanding || null);
      setResults(res.results || []);
      setContextChunks(res.context_chunks || []);
      setLatency(`${res.latency_ms || 0}ms`);
      toast(`Found ${(res.results || []).length} relevant chunks`, "success");
    } catch (error) {
      setContextChunks([]);
      setResults([]);
      toast(error.message, "error");
    } finally {
      setLoading(false);
    }
  };

  const sendToGeneration = () => {
    if (!results.length) return;
    sessionStorage.setItem("rag_query", query.trim());
    sessionStorage.setItem("rag_context", JSON.stringify(results));
    sessionStorage.setItem("rag_context_payload", JSON.stringify(contextChunks));
    navigate("/generation");
  };

  const sidebarFooter = (
    <div className="space-y-4">
      <Link className="block w-full rounded-md bg-primary-container py-3 text-center font-headline text-xs font-bold uppercase tracking-widest text-on-primary-container" to="/generation">Deploy Pipeline</Link>
      <div className="border-t border-slate-800 pt-4 text-xs uppercase tracking-wider text-slate-500">Settings</div>
    </div>
  );

  const topbar = (
    <>
      <div className="flex items-center gap-8">
        <span className="font-headline text-sm font-black tracking-widest text-blue-300">SYSTEM DIAGNOSTICS</span>
        <nav className="flex gap-6 text-xs uppercase tracking-widest">
          <span className="py-1 text-slate-400">Cluster Status</span>
          <span className="border-b border-blue-400 py-1 text-blue-300">Latency</span>
          <span className="py-1 text-slate-400">Tokens</span>
        </nav>
      </div>
      <div className="flex gap-4 text-[10px] uppercase tracking-wider text-slate-500">
        <span>Latency: {latency}</span>
        <span>Recall: {results.length ? (results.length / Number(topK || 1)).toFixed(2) : "—"}</span>
      </div>
    </>
  );

  return (
    <PageShell mainClassName="bg-background overflow-y-auto" sidebarFooter={sidebarFooter} topbar={topbar}>
      <div className="mx-auto max-w-7xl px-12 pb-12 pt-8">
        <div className="mb-10">
          <span className="font-headline text-[10px] font-bold uppercase tracking-[0.3em] text-tertiary">Phase 03</span>
          <h1 className="mt-1 font-headline text-4xl font-bold tracking-tight">Retrieval Pipeline</h1>
        </div>

        <div className="grid grid-cols-12 gap-8">
          <div className="col-span-5 space-y-6">
            <section className="rounded-lg bg-surface-container-low p-6">
              <h3 className="mb-4 font-headline text-xs font-bold uppercase tracking-widest text-tertiary">Query Input</h3>
              <textarea className="w-full resize-none rounded-md border border-outline-variant/20 bg-surface-container-lowest p-4 text-sm" placeholder="Ask something about your documents..." rows="3" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); void run(); } }} />
              <div className="mt-4 grid grid-cols-2 gap-3">
                <input className="rounded bg-surface-container-highest px-2 py-1.5 font-mono text-xs text-primary" type="number" min="1" max="20" value={topK} onChange={(e) => setTopK(e.target.value)} />
                <select className="rounded bg-surface-container-highest px-2 py-1.5 font-mono text-xs text-primary" value={String(rerank)} onChange={(e) => setRerank(e.target.value === "true")}><option value="true">Reranking On</option><option value="false">Reranking Off</option></select>
              </div>
              <button className="mt-4 w-full rounded-md border border-tertiary/30 bg-tertiary/10 py-3 font-headline text-xs uppercase tracking-widest text-tertiary disabled:opacity-50" disabled={loading} onClick={run} type="button">{loading ? "Searching..." : "Run Retrieval"}</button>
            </section>

            <section className="rounded-lg bg-surface-container-low p-6">
              <h3 className="mb-6 font-headline text-xs font-bold uppercase tracking-widest text-primary">Query Understanding</h3>
              <div className="space-y-3 text-xs">
                <div className="flex justify-between rounded bg-surface-container/50 p-3"><span>Rewritten Query</span><span className="max-w-[150px] truncate font-mono text-primary">{understanding?.rewritten_query || "—"}</span></div>
                <div className="flex justify-between rounded bg-surface-container/50 p-3"><span>Intent</span><span className="font-mono text-primary">{understanding?.intent || "—"}</span></div>
                <div className="flex justify-between rounded bg-surface-container/50 p-3"><span>Entities</span><span className="max-w-[150px] truncate font-mono text-primary">{(understanding?.entities || []).slice(0, 3).join(", ") || "—"}</span></div>
                <div className="rounded bg-surface-container/50 p-3"><div className="mb-2 text-[9px] uppercase text-slate-500">Keywords</div><div className="flex flex-wrap gap-1">{(understanding?.keywords || []).length ? understanding.keywords.slice(0, 8).map((k) => <span key={k} className="rounded border border-primary/20 bg-primary/10 px-2 py-0.5 font-mono text-[9px] text-primary">{k}</span>) : <span className="italic text-slate-600">Run a query to populate...</span>}</div></div>
              </div>
            </section>
          </div>

          <div className="col-span-7 space-y-6">
            <section className="glass-panel rounded-xl p-8">
              <div className="mb-6 flex items-center justify-between">
                <div><h3 className="font-headline text-sm font-bold uppercase tracking-wider">Retrieval Results</h3><p className="text-[10px] uppercase text-slate-500">Hybrid Vector + BM25</p></div>
                <span className={`font-mono text-[10px] ${loading ? "text-yellow-400" : "text-green-400"}`}>{loading ? "SEARCHING..." : "DONE"}</span>
              </div>
              <div className="max-h-[520px] space-y-3 overflow-y-auto pr-1">
                {results.length ? results.map((r) => { const score = ((r.rerank_score || r.score || 0) * 100).toFixed(1); const source = r.title || r.source || "Unknown"; return <div key={r.chunk_id} className="result-card rounded-md border-l-2 border-tertiary/30 bg-surface-container-low p-5"><div className="mb-3 flex items-start justify-between"><div className="flex flex-wrap items-center gap-2"><span className={`badge ${badgeClass(r.retrieval_method)}`}>{r.retrieval_method}</span><span className="max-w-[180px] truncate font-mono text-[10px] text-slate-500" title={source}>{source}</span></div><span className="font-mono text-[10px] text-tertiary">{score}%</span></div><p className="line-clamp-3 text-xs leading-relaxed text-on-surface-variant">{r.content || "—"}</p>{r.summary ? <p className="mt-2 text-[10px] italic text-slate-500">{r.summary}</p> : null}</div>; }) : <div className="p-8 text-center text-sm italic text-slate-600">Enter a query to see retrieved chunks...</div>}
              </div>
            </section>

            <section className="grid grid-cols-3 gap-4">
              <div className="rounded-md border-l-2 border-primary bg-surface-container-low p-4"><div className="text-[9px] uppercase tracking-wider text-slate-500">Retrieval Method</div><div className="mt-1 font-headline text-sm font-bold text-primary">{[...new Set(results.map((r) => r.retrieval_method))].join(" + ") || "—"}</div></div>
              <div className="rounded-md border-l-2 border-tertiary bg-surface-container-low p-4"><div className="text-[9px] uppercase tracking-wider text-slate-500">Results Found</div><div className="mt-1 font-headline text-sm font-bold text-tertiary">{results.length ? `${results.length} chunks` : "—"}</div></div>
              <div className="rounded-md border-l-2 border-secondary bg-surface-container-low p-4"><div className="text-[9px] uppercase tracking-wider text-slate-500">Query Time</div><div className="mt-1 font-headline text-sm font-bold text-secondary">{latency}</div></div>
            </section>

            <button className="w-full rounded-md border border-primary/20 bg-surface-container-highest py-3 font-headline text-xs uppercase tracking-widest text-primary disabled:opacity-40" disabled={!results.length} onClick={sendToGeneration} type="button">Send to Generation →</button>
          </div>
        </div>

        <div className="mt-10 flex flex-col items-center py-8">
          <span className="mb-2 font-headline text-[10px] uppercase tracking-[0.3em] text-tertiary">Phase 03 Complete</span>
          <Link className="rounded-md bg-surface-container-highest px-12 py-5 font-headline text-lg font-bold uppercase tracking-widest" to="/generation">Proceed to Generation</Link>
        </div>
      </div>
    </PageShell>
  );
}
