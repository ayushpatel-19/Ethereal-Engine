import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import { GenerationProvider } from "./components/GenerationProvider";
import { ToastProvider } from "./components/ToastProvider";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <GenerationProvider>
          <App />
        </GenerationProvider>
      </ToastProvider>
    </BrowserRouter>
  </React.StrictMode>
);
