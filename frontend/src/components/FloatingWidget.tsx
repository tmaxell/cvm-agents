import { useMemo, useState } from "react";
import { ChatWorkspacePage } from "../pages/chat-workspace/ChatWorkspacePage";

type WidgetTab = "chat" | "artifacts";

export function FloatingWidget() {
  const [collapsed, setCollapsed] = useState(false);
  const [activeTab, setActiveTab] = useState<WidgetTab>("chat");
  const [size, setSize] = useState({ width: 900, height: 640 });

  const style = useMemo(() => ({ width: `${size.width}px`, height: collapsed ? "56px" : `${size.height}px` }), [collapsed, size]);

  return (
    <section className="floating-widget-shell" style={style}>
      <header className="floating-widget-header" data-drag-handle="true">
        <strong>Chat assistant</strong>
        <div className="floating-widget-actions">
          <button onClick={() => setCollapsed((v) => !v)}>{collapsed ? "Развернуть" : "Свернуть"}</button>
        </div>
      </header>

      {!collapsed && (
        <>
          <nav className="floating-widget-tabs" aria-label="Widget mode tabs">
            <button className={activeTab === "chat" ? "active" : ""} onClick={() => setActiveTab("chat")}>Чат</button>
            <button className={activeTab === "artifacts" ? "active" : ""} onClick={() => setActiveTab("artifacts")}>Артефакты</button>
          </nav>
          <div className="floating-widget-content">
            {activeTab === "chat" ? <ChatWorkspacePage /> : <div className="floating-widget-placeholder">Артефакты доступны в правой панели чата.</div>}
          </div>
          <footer className="floating-widget-resize">
            <label>
              Ширина
              <input type="range" min={720} max={1200} value={size.width} onChange={(e) => setSize((prev) => ({ ...prev, width: Number(e.target.value) }))} />
            </label>
            <label>
              Высота
              <input type="range" min={480} max={900} value={size.height} onChange={(e) => setSize((prev) => ({ ...prev, height: Number(e.target.value) }))} />
            </label>
          </footer>
        </>
      )}
    </section>
  );
}
