import { createContext, useContext, useMemo, useState } from "react";

const ToastContext = createContext(() => {});

const COLORS = {
  info: "border-blue-400 text-blue-300",
  success: "border-green-400 text-green-300",
  error: "border-red-400 text-red-300",
  warning: "border-yellow-400 text-yellow-300"
};

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const pushToast = useMemo(
    () => (message, type = "info") => {
      const id = Date.now() + Math.random();
      setToasts((current) => [...current, { id, message, type }]);
      window.setTimeout(() => {
        setToasts((current) => current.filter((toast) => toast.id !== id));
      }, 4000);
    },
    []
  );

  return (
    <ToastContext.Provider value={pushToast}>
      {children}
      <div className="fixed bottom-24 right-8 z-[100] flex max-w-sm flex-col gap-3">
        {toasts.map((toast) => (
          <div
            className={`rounded-md border-l-2 bg-slate-900/90 px-6 py-4 font-mono text-xs shadow-2xl backdrop-blur ${COLORS[toast.type] || COLORS.info}`}
            key={toast.id}
          >
            {toast.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
