import { Link, useLocation } from "react-router-dom";

const NAV_ITEMS = [
  { label: "Ingestion", path: "/", icon: "input" },
  { label: "Storage", path: "/storage", icon: "database" },
  { label: "Retrieval", path: "/retrieval", icon: "search" },
  { label: "Generation", path: "/generation", icon: "psychology" }
];

export default function Sidebar({ extra, footer }) {
  const location = useLocation();

  return (
    <aside className="fixed left-0 top-0 z-50 flex h-screen w-72 flex-col bg-slate-950/40 py-8 shadow-[20px_0_40px_rgba(0,0,0,0.4)] backdrop-blur-xl">
      <div className="mb-12 px-8">
        <div className="font-headline text-xl font-bold tracking-widest text-blue-300">
          ETHEREAL ENGINE
        </div>
        <div className="mt-1 font-headline text-[10px] uppercase tracking-[0.2em] text-slate-500">
          RAG Pipeline v2.4
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-4">
        {NAV_ITEMS.map((item) => {
          const active = location.pathname === item.path;
          return (
            <Link
              className={
                active
                  ? "flex items-center gap-4 border-l-4 border-blue-400 bg-blue-500/10 px-4 py-3 text-blue-200 transition-all duration-300"
                  : "flex items-center gap-4 px-4 py-3 text-slate-500 transition-all duration-300 hover:bg-slate-800/50 hover:text-slate-300"
              }
              key={item.path}
              to={item.path}
            >
              <span className={`material-symbols-outlined ${active ? "text-blue-300" : ""}`}>
                {item.icon}
              </span>
              <span className="font-headline text-sm tracking-tight">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      {extra ? <div className="px-6 pb-4">{extra}</div> : null}

      <div className="mt-auto px-6">{footer}</div>
    </aside>
  );
}
