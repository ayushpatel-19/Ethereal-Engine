import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import PageShell from "../components/PageShell";
import { useToast } from "../components/ToastProvider";
import { api, pipelineSocket } from "../lib/api";

const ts = () => new Date().toLocaleTimeString("en", { hour12: false });

export default function IngestionPage() {
  const toast = useToast();
  const fileRef = useRef(null);
  const [files, setFiles] = useState([]);
  const [stats, setStats] = useState(null);
  const [sources, setSources] = useState([]);
  const [events, setEvents] = useState([]);
  const [strategy, setStrategy] = useState("semantic");
  const [progress, setProgress] = useState({ visible: false, percent: 0, message: "" });
  const [busy, setBusy] = useState({ file: false, url: false, api: false });
  const [urlForm, setUrlForm] = useState({ url: "", depth: 1, pages: 5, status: "Ready to crawl..." });
  const [apiForm, setApiForm] = useState({ endpoint: "", method: "GET", jsonPath: "", status: "Idle" });
  const [metrics, setMetrics] = useState({ chunks: 0, tokens: "—", keywords: [] });
  const [running, setRunning] = useState(false);
  const fileLabel = useMemo(() => files.map((f) => f.name).join(", "), [files]);

  const log = (message, type = "info") =>
    setEvents((v) => [...v, { id: Date.now() + Math.random(), type, message, time: ts() }]);

  const loadStats = async () => {
    try {
      setStats(await api.system.stats());
    } catch {
      setStats(null);
    }
  };

  const loadSources = async () => {
    try {
      const res = await api.ingestion.listSources();
      setSources(res.sources || []);
    } catch {
      setSources([]);
    }
  };

  useEffect(() => {
    void loadStats();
    void loadSources();
    const unsub = pipelineSocket.subscribe("*", (event) => {
      if (!event?.message) return;
      log(event.message, event.event_type);
      if (event.event_type === "complete") {
        setRunning(false);
        setProgress((v) => ({ ...v, visible: false }));
        void loadStats();
        void loadSources();
        toast(event.message, "success");
      }
      if (event.event_type === "error") {
        setRunning(false);
        setProgress((v) => ({ ...v, visible: false }));
        toast(event.message, "error");
      }
    });
    const interval = window.setInterval(loadStats, 10000);
    return () => {
      unsub();
      window.clearInterval(interval);
    };
  }, [toast]);

  const pollProgress = () => {
    const timer = window.setInterval(async () => {
      try {
        const state = await api.system.progress();
        if (state.status !== "idle") {
          setProgress({ visible: true, percent: state.progress || 0, message: state.message || "" });
        }
        if (state.status === "done" || state.status === "error") {
          window.clearInterval(timer);
          window.setTimeout(() => setProgress((v) => ({ ...v, visible: false })), 1200);
        }
      } catch {}
    }, 1200);
    return timer;
  };

  const onFileUpload = async () => {
    if (!files.length) return toast("Please select a file first", "warning");
    setBusy((v) => ({ ...v, file: true }));
    setRunning(true);
    const timer = pipelineSocket.isOpen() ? null : pollProgress();
    try {
      for (const file of files) {
        const res = await api.ingestion.uploadFile(file, strategy);
        setMetrics({ chunks: res.chunk_count || 0, tokens: "~512 avg", keywords: res.keywords || [] });
        log(`Done! ${res.chunk_count} chunks stored.`, "complete");
      }
      setFiles([]);
      void loadStats();
      void loadSources();
    } catch (error) {
      setRunning(false);
      toast(error.message, "error");
    } finally {
      if (timer) window.clearInterval(timer);
      setBusy((v) => ({ ...v, file: false }));
    }
  };

  const onUrlIngest = async () => {
    if (!urlForm.url.trim()) return toast("Enter a URL first", "warning");
    setBusy((v) => ({ ...v, url: true }));
    setRunning(true);
    setUrlForm((v) => ({ ...v, status: `Crawling ${v.url}...` }));
    try {
      const res = await api.ingestion.ingestURL(urlForm.url, Number(urlForm.depth), Number(urlForm.pages));
      setUrlForm((v) => ({ ...v, status: `Done: ${res.doc_count} pages, ${res.chunk_count} chunks` }));
      void loadStats();
      void loadSources();
      setRunning(false);
    } catch (error) {
      setRunning(false);
      setUrlForm((v) => ({ ...v, status: `Error: ${error.message}` }));
      toast(error.message, "error");
    } finally {
      setBusy((v) => ({ ...v, url: false }));
    }
  };

  const onApiIngest = async () => {
    if (!apiForm.endpoint.trim()) return toast("Enter an API endpoint", "warning");
    setBusy((v) => ({ ...v, api: true }));
    setRunning(true);
    setApiForm((v) => ({ ...v, status: "Fetching..." }));
    try {
      const res = await api.ingestion.ingestAPI(apiForm.endpoint, apiForm.method, {}, null, apiForm.jsonPath || null);
      setApiForm((v) => ({ ...v, status: `Done: ${res.chunk_count} chunks` }));
      void loadStats();
      void loadSources();
      setRunning(false);
    } catch (error) {
      setRunning(false);
      setApiForm((v) => ({ ...v, status: `Error: ${error.message}` }));
      toast(error.message, "error");
    } finally {
      setBusy((v) => ({ ...v, api: false }));
    }
  };

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
          <span className="border-b border-blue-400 py-1 text-blue-300">Cluster Status</span>
          <span className="py-1 text-slate-400">Latency</span>
          <span className="py-1 text-slate-400">Tokens</span>
        </nav>
      </div>
      <div className="flex items-center gap-4 text-[10px] uppercase tracking-wider text-slate-500">
        <span>Retrieval backend: {stats?.ollama_status || "—"}</span>
        <span>Chunks: {stats?.total_chunks || 0}</span>
      </div>
    </>
  );

  return (
    <PageShell mainClassName="bg-background relative overflow-y-auto" sidebarFooter={sidebarFooter} topbar={topbar}>
      <div className="mx-auto max-w-7xl px-12 py-10">
        <div className="mb-8 flex items-end justify-between">
          <div>
            <span className="font-mono text-sm uppercase tracking-widest text-primary">Phase 01 // Data Ingestion</span>
            <h2 className="font-headline text-5xl font-bold tracking-tight">The Inflow Stream</h2>
            <p className="mt-2 max-w-lg text-slate-400">Upload files, crawl URLs, or connect APIs without changing the existing Ethereal visual language.</p>
          </div>
          <div className="border-l-2 border-primary bg-surface-container-low px-4 py-2 font-headline text-[10px] uppercase tracking-widest text-slate-400">Chunks: {stats?.total_chunks || 0}</div>
        </div>

        <div className="mb-10 grid grid-cols-12 gap-6">
          <section className="col-span-5 rounded-md bg-surface-container-low p-6">
            <div className="mb-5 flex items-center gap-3"><span className="material-symbols-outlined text-primary">upload_file</span><h3 className="font-headline text-lg font-bold">File Upload</h3></div>
            <div className="drop-zone flex min-h-[140px] cursor-pointer flex-col items-center justify-center gap-3 rounded-md p-6" onClick={() => fileRef.current?.click()} onDrop={(e) => { e.preventDefault(); setFiles(Array.from(e.dataTransfer.files || [])); }} onDragOver={(e) => e.preventDefault()}>
              <span className="material-symbols-outlined text-4xl text-slate-500">cloud_upload</span>
              <p className="font-headline text-xs uppercase tracking-wider text-slate-500">{files.length ? `${files.length} file(s) selected` : "Drop files or click to browse"}</p>
              {fileLabel ? <p className="font-mono text-[10px] text-primary">{fileLabel}</p> : null}
            </div>
            <input ref={fileRef} className="hidden" multiple type="file" onChange={(e) => setFiles(Array.from(e.target.files || []))} />
            <div className="mt-4 flex items-center gap-2"><span className="font-headline text-[10px] uppercase text-slate-500">Strategy:</span><select className="rounded bg-surface-container-highest px-2 py-1 font-mono text-xs text-primary" value={strategy} onChange={(e) => setStrategy(e.target.value)}><option value="semantic">Semantic</option><option value="overlap">Overlap</option><option value="fixed">Fixed</option><option value="parent_child">Parent-Child</option></select></div>
            <button className="mt-4 w-full rounded-md border border-primary/30 bg-primary/10 py-2.5 font-headline text-xs uppercase tracking-widest text-primary disabled:opacity-50" disabled={busy.file} onClick={onFileUpload} type="button">{busy.file ? "Ingesting..." : "Ingest File"}</button>
          </section>

          <section className="col-span-4 rounded-md bg-surface-container-low p-6">
            <div className="mb-5 flex items-center gap-3"><span className="material-symbols-outlined text-tertiary">travel_explore</span><h3 className="font-headline text-lg font-bold">Web Crawler</h3></div>
            <input className="mb-3 w-full rounded bg-surface-container-highest px-3 py-2 font-mono text-xs" placeholder="https://docs.example.com" value={urlForm.url} onChange={(e) => setUrlForm((v) => ({ ...v, url: e.target.value }))} />
            <div className="mb-3 grid grid-cols-2 gap-2">
              <input className="rounded bg-surface-container-highest px-2 py-1.5 font-mono text-xs text-primary" type="number" min="1" max="3" value={urlForm.depth} onChange={(e) => setUrlForm((v) => ({ ...v, depth: e.target.value }))} />
              <input className="rounded bg-surface-container-highest px-2 py-1.5 font-mono text-xs text-primary" type="number" min="1" max="20" value={urlForm.pages} onChange={(e) => setUrlForm((v) => ({ ...v, pages: e.target.value }))} />
            </div>
            <div className="mb-3 rounded border-l-2 border-tertiary bg-surface-container-highest p-3 font-mono text-[9px] text-tertiary">{urlForm.status}</div>
            <button className="w-full rounded-md border border-tertiary/30 bg-tertiary/10 py-2.5 font-headline text-xs uppercase tracking-widest text-tertiary disabled:opacity-50" disabled={busy.url} onClick={onUrlIngest} type="button">{busy.url ? "Crawling..." : "Start Crawl"}</button>
          </section>

          <section className="col-span-3 rounded-md bg-surface-container-low p-6">
            <div className="mb-5 flex items-center gap-3"><span className="material-symbols-outlined text-secondary">api</span><h3 className="font-headline text-lg font-bold">REST API</h3></div>
            <input className="mb-2 w-full rounded bg-surface-container-highest px-2 py-2 font-mono text-[10px]" placeholder="https://api.example.com/data" value={apiForm.endpoint} onChange={(e) => setApiForm((v) => ({ ...v, endpoint: e.target.value }))} />
            <select className="mb-2 w-full rounded bg-surface-container-highest px-2 py-1.5 font-mono text-[10px] text-secondary" value={apiForm.method} onChange={(e) => setApiForm((v) => ({ ...v, method: e.target.value }))}><option value="GET">GET</option><option value="POST">POST</option></select>
            <input className="mb-3 w-full rounded bg-surface-container-highest px-2 py-1.5 font-mono text-[10px]" placeholder="JSON path e.g. $.data" value={apiForm.jsonPath} onChange={(e) => setApiForm((v) => ({ ...v, jsonPath: e.target.value }))} />
            <div className="mb-3 rounded border border-outline-variant/20 bg-slate-950/30 p-2 font-mono text-[9px] text-secondary">{apiForm.status}</div>
            <button className="w-full rounded-md border border-secondary/30 bg-secondary/10 py-2.5 font-headline text-xs uppercase tracking-widest text-secondary disabled:opacity-50" disabled={busy.api} onClick={onApiIngest} type="button">{busy.api ? "Connecting..." : "Connect"}</button>
          </section>
        </div>

        <div className="mb-10 grid grid-cols-2 gap-10">
          <section className="glass-panel rounded-xl border-l-4 border-primary p-8">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="font-headline text-xl font-bold uppercase">Pipeline Feed</h3>
              <span className={`font-headline text-[10px] uppercase ${running ? "text-primary" : "text-green-300"}`}>{running ? "Running" : "Done"}</span>
            </div>
            {progress.visible ? <div className="mb-4"><div className="mb-1 font-mono text-[10px] text-slate-400">{progress.message}</div><div className="h-1.5 rounded bg-slate-800"><div className="h-full rounded bg-gradient-to-r from-indigo-500 to-violet-500" style={{ width: `${progress.percent}%` }} /></div></div> : null}
            <div className="max-h-48 space-y-2 overflow-y-auto font-mono text-xs">{events.length ? events.map((e) => <div key={e.id} className="flex gap-3"><span className="text-slate-600">[{e.time}]</span><span>{e.message}</span></div>) : <div className="italic text-slate-600">Awaiting pipeline events...</div>}</div>
          </section>

          <section className="glass-panel rounded-xl border-l-4 border-secondary p-8">
            <h3 className="mb-4 font-headline text-xl font-bold uppercase">Chunk Metrics</h3>
            <div className="space-y-4">
              <div className="flex items-end justify-between"><span className="font-headline text-[10px] uppercase text-slate-500">Total Chunks</span><span className="font-headline text-2xl font-bold text-secondary">{metrics.chunks}</span></div>
              <div className="flex items-end justify-between"><span className="font-headline text-[10px] uppercase text-slate-500">Avg Token Size</span><span className="font-headline text-lg text-primary">{metrics.tokens}</span></div>
              <div className="flex flex-wrap gap-1">{metrics.keywords.length ? metrics.keywords.slice(0, 10).map((k) => <span key={k} className="rounded border border-secondary/20 bg-secondary/10 px-2 py-0.5 font-mono text-[9px] text-secondary">{k}</span>) : <span className="text-[9px] italic text-slate-600">Keywords will appear here...</span>}</div>
            </div>
          </section>
        </div>

        <section className="mb-12">
          <div className="mb-4 flex items-center justify-between"><h3 className="font-headline text-lg font-bold uppercase">Ingested Sources</h3><button className="font-headline text-[10px] uppercase tracking-wider text-slate-500" onClick={loadSources} type="button">Refresh</button></div>
          <div className="overflow-hidden rounded-md bg-surface-container-low">
            <table className="w-full font-mono text-xs">
              <thead><tr className="border-b border-outline-variant/20"><th className="px-6 py-3 text-left font-headline text-[10px] uppercase text-slate-500">Source</th><th className="px-6 py-3 text-left font-headline text-[10px] uppercase text-slate-500">Type</th><th className="px-6 py-3 text-left font-headline text-[10px] uppercase text-slate-500">Chunks</th><th className="px-6 py-3 text-left font-headline text-[10px] uppercase text-slate-500">Actions</th></tr></thead>
              <tbody>{sources.length ? sources.map((s) => <tr key={s.source} className="border-b border-outline-variant/10"><td className="max-w-[200px] truncate px-6 py-3 text-primary" title={s.source}>{s.title || s.source}</td><td className="px-6 py-3">{s.source_type}</td><td className="px-6 py-3 text-secondary">{s.chunk_count}</td><td className="px-6 py-3"><button className="font-headline text-[10px] uppercase tracking-wider text-red-400" onClick={() => api.ingestion.deleteSource(s.source).then(loadSources)} type="button">Delete</button></td></tr>) : <tr><td className="px-6 py-8 text-center italic text-slate-600" colSpan="4">No sources ingested yet</td></tr>}</tbody>
            </table>
          </div>
        </section>

        <div className="flex flex-col items-center py-8">
          <span className="mb-2 font-headline text-[10px] uppercase tracking-[0.3em] text-primary">Phase 01 Complete</span>
          <Link className="rounded-md bg-surface-container-highest px-12 py-5 font-headline text-lg font-bold uppercase tracking-widest" to="/storage">Proceed to Storage</Link>
        </div>
      </div>
    </PageShell>
  );
}
