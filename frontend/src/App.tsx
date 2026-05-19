import { Navigate, Route, Routes } from "react-router-dom";
import { ChatWorkspacePage } from "./pages/chat-workspace/ChatWorkspacePage";
import { FloatingWidget } from "./components/FloatingWidget";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/chat" replace />} />
      <Route path="/chat" element={<ChatWorkspacePage />} />
      <Route path="/chat/:sessionId" element={<ChatWorkspacePage />} />
      <Route path="/widget" element={<FloatingWidget />} />
      <Route path="/widget/:sessionId" element={<FloatingWidget />} />
    </Routes>
  );
}

export default App;
