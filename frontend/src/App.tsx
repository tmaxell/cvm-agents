import { Navigate, Route, Routes } from "react-router-dom";
import { ChatWorkspacePage } from "./pages/chat-workspace/ChatWorkspacePage";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/chat" replace />} />
      <Route path="/chat" element={<ChatWorkspacePage />} />
      <Route path="/chat/:sessionId" element={<ChatWorkspacePage />} />
    </Routes>
  );
}

export default App;
