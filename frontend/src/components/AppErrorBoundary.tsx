import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  title?: string;
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error("UI boundary error:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <section className="chat-boundary-error">
          <h3>{this.props.title ?? "Часть интерфейса временно недоступна"}</h3>
          <p>Попробуйте обновить страницу или повторить действие позже.</p>
        </section>
      );
    }
    return this.props.children;
  }
}
