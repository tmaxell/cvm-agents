export type ChatRenderMode = "full-page" | "widget";

export const CHAT_MODE_ROUTES = {
  widgetPrefixes: ["/widget"],
  fullPagePrefixes: ["/chat"],
} as const;

export function detectChatRenderMode(pathname: string, widgetRootDetected: boolean): ChatRenderMode {
  if (widgetRootDetected) return "widget";
  if (CHAT_MODE_ROUTES.widgetPrefixes.some((prefix) => pathname.startsWith(prefix))) return "widget";
  return "full-page";
}
