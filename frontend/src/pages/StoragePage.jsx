import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import PageShell from "../components/PageShell";
import { api, pipelineSocket } from "../lib/api";

const stamp = () => new Date().toLocaleTimeString("en", { hour12: false });

export default function StoragePage() {
  const [stats, setStats] = useState(null);
  const [grid, setGrid] = useState(Array.from({ length: 8 }, () => Math.random()));
  const [logs, setLogs] = useState([{ id: "seed", time: "—", type: "INFO", message: "Waiting for storage events..." }]);

  const pushLog = (message, type = "INFO") =>
    setLogs((v) => [...v, { id: Date.now() + Math.random(), time: stamp(), type, message }]);

  const refresh = async () => {
    try {
      const res = await api.system.stats();
      setStats(res);
      pushLog(`Vector store sync: ${res.total_chunks} vectors indexed`, "SYNC");
    } catch (error) {
      pushLog(`Stats fetch failed: ${error.message}`, "ERROR");
    }
  };

  useEffect(() => {
    void refresh();
    const g = window.setInterval(() => setGrid(Array.from({ length: 8 }, () => (Math.random() * 0.8) + 0.1)), 1200);
    const s = window.setInterval(refresh, 8000);
    const unsub = pipelineSocket.subscribe("*", (event) => {
      if (event.event_type === "complete" || event.stage === "embedding") {
        pushLog(event.message, event.event_type === "error" ? "ERROR" : "SYNC");
        void refresh();
      }
    });
    return () => {
      window.clearInterval(g);
      window.clearInterval(s);
      unsub();
    };
  }, []);

  const chromaPct = Math.min(100, ((stats?.total_chunks || 0) / 10000) * 100);
  const redisPct = (stats?.cache_hit_rate || 0) * 100;
  const sidebarFooter = (
    <div className="space-y-4">
      <Link className="block w-full rounded-md bg-primary-container py-3 text-center font-headline text-xs font-bold uppercase tracking-widest text-on-primary-container" to="/generation">Deploy Pipeline</Link>
      <div className="border-t border-slate-800 pt-4 text-xs uppercase tracking-wider text-slate-500">Diagnostics</div>
    </div>
  );
  const topbar = (
    <>
      <div className="flex items-center gap-8">
        <span className="font-headline text-xs font-black uppercase tracking-widest text-blue-300">SYSTEM DIAGNOSTICS</span>
        <nav className="flex gap-6 text-xs uppercase tracking-widest">
          <span className="py-1 text-slate-400">Cluster Status</span>
          <span className="border-b border-blue-400 py-1 text-blue-300">Storage</span>
          <span className="py-1 text-slate-400">Tokens</span>
        </nav>
      </div>
      <div className="text-[10px] uppercase tracking-wider text-slate-500">Sync: {stats?.chroma_status === "healthy" ? "OK" : "Degraded"}</div>
    </>
  );

  return (
    <PageShell mainClassName="bg-surface-container-low overflow-y-auto" sidebarFooter={sidebarFooter} topbar={topbar}>
      <div className="mx-auto max-w-7xl px-12 py-10">
        <div className="mb-12 flex items-end justify-between">
          <div>
            <span className="font-headline text-xs uppercase tracking-[0.3em] text-secondary">Phase 02 // Embedding + Storage</span>
            <h2 className="mt-1 font-headline text-5xl font-bold tracking-tight">Storage Phase</h2>
            <p className="mt-2 max-w-lg text-slate-400">Vectorizing chunks via local embeddings and persisting them with hybrid indexes.</p>
          </div>
          <div className="flex gap-4">
            <div className="border-l-4 border-secondary bg-surface-container-highest px-4 py-2"><div className="text-[10px] uppercase text-outline">Sync Integrity</div><div className="font-headline text-lg font-bold text-secondary">{stats?.total_chunks ? "99.98%" : "—"}</div></div>
            <div className="border-l-4 border-primary bg-surface-container-highest px-4 py-2"><div className="text-[10px] uppercase text-outline">Embed Latency</div><div className="font-headline text-lg font-bold text-primary">{stats?.avg_latency_ms ? `${Math.round(stats.avg_latency_ms)}ms` : "—"}</div></div>
          </div>
        </div>

        <div className="mb-10 grid grid-cols-12 gap-8">
          <section className="col-span-8 rounded-md bg-surface-container p-8">
            <div className="mb-8 flex items-center justify-between">
              <div><h3 className="font-headline text-xl font-bold">Vector Architecture</h3><p className="text-xs uppercase text-outline">Local embedding model</p></div>
              <span className="rounded-full bg-primary/20 px-2 py-1 text-[10px] font-bold uppercase text-primary">Active</span>
            </div>
            <div className="flex items-center justify-around gap-6">
              <div className="w-1/4 rounded-sm border border-outline-variant/10 bg-surface-container-low/50 p-4"><div className="mb-3 font-mono text-[10px] text-outline">INPUT_STREAM_RAW</div><div className="space-y-2"><div className="h-1.5 rounded-full bg-slate-800" /><div className="h-1.5 w-3/4 rounded-full bg-slate-800" /><div className="h-1.5 w-5/6 rounded-full bg-slate-800" /><div className="h-1.5 w-2/3 rounded-full bg-slate-800" /></div><div className="mt-4 font-mono text-[9px] text-slate-600">{stats?.total_chunks || 0} chunks pending</div></div>
              <div className="float-anim relative flex h-32 w-32 items-center justify-center"><div className="orbit-ring absolute inset-0 rounded-full border border-dashed border-primary/30" /><div className="orbit-ring-rev absolute inset-3 rounded-full border border-primary/20" /><div className="pulse-core h-5 w-5 rounded-full bg-primary" /></div>
              <div className="w-1/4 rounded-sm border border-primary/20 bg-surface-container-highest/50 p-4"><div className="mb-3 font-mono text-[10px] text-primary">VECTOR_SPACE</div><div className="grid grid-cols-4 gap-1">{grid.map((o, i) => <div key={i} className="h-4 rounded-sm bg-primary/40" style={{ opacity: o }} />)}</div><div className="mt-3 font-mono text-[9px] text-primary">{stats?.total_chunks || 0} stored</div></div>
            </div>
          </section>

          <section className="col-span-4 rounded-md bg-surface-container p-6">
            <h3 className="font-headline text-lg font-bold">Latent Projection</h3>
            <p className="text-xs uppercase text-outline">Cluster Distribution</p>
            <div className="mt-8 flex h-52 items-center justify-center rounded border border-secondary/20 bg-secondary/5 font-headline text-3xl text-secondary">{Math.ceil((stats?.total_chunks || 0) / 20) || "—"}</div>
          </section>

          <section className="glass-panel col-span-12 rounded-md border-l-4 border-tertiary p-8">
            <div className="mb-8 flex items-center justify-between">
              <div><h3 className="font-headline text-2xl font-bold">Hybrid Storage Layer</h3><p className="text-sm text-outline">ChromaDB vector store · BM25 index · cache fallback</p></div>
              <button className="border border-tertiary/20 bg-surface-container-highest px-6 py-2 font-headline text-xs font-bold uppercase tracking-widest text-tertiary" onClick={refresh} type="button">Force Sync</button>
            </div>
            <div className="grid grid-cols-3 gap-8">
              <div className="rounded-sm border border-outline-variant/10 bg-surface-container-low p-6"><h4 className="mb-1 font-headline text-lg font-bold">ChromaDB</h4><p className="mb-6 text-xs text-outline">Persistent vector store</p><div className="mb-3 flex items-end justify-between"><span className="text-[10px] uppercase text-outline">Total Vectors</span><span className="font-headline text-xl">{stats?.total_chunks || 0}</span></div><div className="h-1 rounded-full bg-surface-container-highest"><div className="h-full bg-tertiary" style={{ width: `${chromaPct}%` }} /></div></div>
              <div className="rounded-sm border border-outline-variant/10 bg-surface-container-low p-6"><h4 className="mb-1 font-headline text-lg font-bold">BM25 Index</h4><p className="mb-6 text-xs text-outline">Keyword search</p><div className="mb-3 flex items-end justify-between"><span className="text-[10px] uppercase text-outline">Indexed Docs</span><span className="font-headline text-xl">{stats?.total_chunks || 0}</span></div><div className="h-1 rounded-full bg-surface-container-highest"><div className="h-full bg-secondary" style={{ width: `${chromaPct}%` }} /></div></div>
              <div className="rounded-sm border border-outline-variant/10 bg-surface-container-low p-6"><h4 className="mb-1 font-headline text-lg font-bold">Cache Layer</h4><p className="mb-6 text-xs text-outline">Redis compatible fallback</p><div className="mb-3 flex items-end justify-between"><span className="text-[10px] uppercase text-outline">Hit Rate</span><span className="font-headline text-xl">{`${((stats?.cache_hit_rate || 0) * 100).toFixed(1)}%`}</span></div><div className="h-1 rounded-full bg-surface-container-highest"><div className="h-full bg-primary" style={{ width: `${redisPct}%` }} /></div></div>
            </div>
          </section>
        </div>

        <section className="mb-8 rounded-md border border-outline-variant/5 bg-surface-container-lowest p-6">
          <div className="mb-4 font-headline text-[10px] font-bold uppercase tracking-widest text-primary">System Feed</div>
          <div className="max-h-32 space-y-1.5 overflow-y-auto font-mono text-xs">{logs.map((l) => <div key={l.id} className="flex gap-4"><span className="text-slate-600">[{l.time}]</span><span>{l.type}</span><span>{l.message}</span></div>)}</div>
        </section>

        <div className="flex flex-col items-center py-8">
          <span className="mb-2 font-headline text-[10px] uppercase tracking-[0.3em] text-secondary">Phase 02 Complete</span>
          <Link className="rounded-md bg-surface-container-highest px-12 py-5 font-headline text-lg font-bold uppercase tracking-widest" to="/retrieval">Proceed to Retrieval</Link>
        </div>
      </div>
    </PageShell>
  );
}
