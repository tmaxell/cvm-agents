import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { detectChatRenderMode } from "./config/chatMode";
import "./index.css";

const legacyCssEnabled = import.meta.env.VITE_ENABLE_LEGACY_CSS === "true";
const widgetShellEnabled = import.meta.env.VITE_WIDGET_SHELL_ENABLED !== "false";

function isWidgetMode(): boolean {
  if (typeof window === "undefined") return false;
  const root = document.getElementById("root");
  const hasWidgetClass = document.body.classList.contains("floating-widget-root")
    || document.documentElement.classList.contains("floating-widget-root")
    || root?.classList.contains("floating-widget-root")
    || root?.parentElement?.classList.contains("floating-widget-root");

  return detectChatRenderMode(window.location.pathname, hasWidgetClass) === "widget";
}

if (legacyCssEnabled && widgetShellEnabled && isWidgetMode()) {
  void import("./styles/widget-shell.css");
}

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
