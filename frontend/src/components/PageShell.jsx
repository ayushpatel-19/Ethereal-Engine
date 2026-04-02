import Sidebar from "./Sidebar";

export function TopBar({ children }) {
  return (
    <header className="fixed right-0 top-0 z-40 flex h-16 w-[calc(100%-18rem)] items-center justify-between bg-slate-950/20 px-12 backdrop-blur-md">
      {children}
    </header>
  );
}

export function MainFrame({ children, className = "" }) {
  return <main className={`min-h-screen pl-72 pt-16 ${className}`}>{children}</main>;
}

export default function PageShell({ sidebarExtra, sidebarFooter, topbar, children, mainClassName = "" }) {
  return (
    <>
      <Sidebar extra={sidebarExtra} footer={sidebarFooter} />
      <TopBar>{topbar}</TopBar>
      <MainFrame className={mainClassName}>{children}</MainFrame>
    </>
  );
}
