import { getAssistantPlainText } from "./markdown";

type ChatDisplayOptions = {
  preserveSourceMarkers?: boolean;
  parseSourceMarkers?: boolean;
  preserveFormatting?: boolean;
};

export function getChatDisplayContent(content: string, isTyping = false, options: ChatDisplayOptions = {}): string {
  // User messages are literal, including formatting and source-token text.
  if (options.parseSourceMarkers === false) return content;

  let displayContent = content.replace(/<think>[\s\S]*?(?:<\/think>\n*|$)/gi, "");

  // A partially typed opening tag is not visible answer content yet.
  if (isTyping) {
    displayContent = displayContent.replace(/<(?:t(?:h(?:i(?:n(?:k)?)?)?)?)?$/i, "");
  }

  // The gradual renderer can stop anywhere inside an internal citation. Do
  // not briefly expose its token, even if a malformed answer ends there.
  const markerStart = displayContent.lastIndexOf("[[");
  if (markerStart !== -1) {
    const tail = displayContent.slice(markerStart).toLowerCase();
    if ("[[source:".startsWith(tail) || (tail.startsWith("[[source:") && !tail.includes("]]"))) {
      displayContent = displayContent.slice(0, markerStart);
    }
  }
  if (isTyping) displayContent = displayContent.replace(/\[$/, "");
  if (!options.preserveSourceMarkers) {
    displayContent = displayContent.replace(/\[\[source:[\s\S]*?\]\]/gi, "");
  }

  // The renderer validates citations before Markdown normalization. Nothing
  // here may turn an invalid token (for example a*b) into a valid one.
  if (options.preserveFormatting) return displayContent.trim();
  if (options.preserveSourceMarkers) {
    return displayContent.split(/(\[\[source:[\s\S]*?\]\])/gi).map((part) =>
      /^\[\[source:[\s\S]*?\]\]$/i.test(part) ? part : getAssistantPlainText(part, isTyping, false),
    ).join("").trim();
  }
  return getAssistantPlainText(displayContent, isTyping);
}
