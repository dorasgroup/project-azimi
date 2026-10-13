import type { ChatSource } from "@/features/chat/types/chat.types";

export type ArticleCitation = {
  id: string;
  title: string;
  url: string;
  excerpt: string | null;
};

export type AnswerPart = { type: "text"; text: string } | { type: "source"; source: ArticleCitation };

const citationToken = /^[A-Za-z0-9_-]{1,64}$/;

export function getArticleCitations(sources: ChatSource[] | undefined): Map<string, ArticleCitation> {
  const eligible = new Map<string, ArticleCitation>();
  const ambiguous = new Set<string>();
  for (const source of Array.isArray(sources) ? sources : []) {
    if (!source || source.source_type !== "haminyar_article" ||
      typeof source.citation_id !== "string" || !citationToken.test(source.citation_id) ||
      typeof source.source_url !== "string") continue;
    if (/[\u0000-\u0020\u007f\\]/.test(source.source_url)) continue;

    let url: URL;
    try { url = new URL(source.source_url); } catch { continue; }
    if (url.protocol !== "https:" || !["haminyar.ir", "www.haminyar.ir"].includes(url.hostname) ||
      url.username || url.password || (url.port && url.port !== "443")) continue;

    const citation: ArticleCitation = {
      id: source.citation_id,
      title: typeof source.article_title === "string" && source.article_title.trim()
        ? source.article_title.trim() : "مقاله سایت حامین یار",
      url: url.href,
      excerpt: typeof source.content === "string" && source.content.trim() ? source.content.trim() : null,
    };
    const existing = eligible.get(citation.id);
    // Refuse conflicting metadata rather than attach a paragraph to an
    // arbitrary article. Exact duplicate records are harmless.
    if (existing && (existing.url !== citation.url || existing.title !== citation.title)) {
      ambiguous.add(citation.id);
      eligible.delete(citation.id);
    } else if (!ambiguous.has(citation.id)) {
      eligible.set(citation.id, citation);
    }
  }
  return eligible;
}

export function getAnswerParts(content: string, sources: ChatSource[] | undefined): AnswerPart[] {
  const eligible = getArticleCitations(sources);
  const parts: AnswerPart[] = [];
  const markers = /\[\[source:([\s\S]*?)\]\]/gi;
  let offset = 0;
  let previousCitation: string | null = null;

  for (const match of content.matchAll(markers)) {
    const text = content.slice(offset, match.index);
    if (text) {
      parts.push({ type: "text", text });
      if (text.trim()) previousCitation = null;
    }
    const token = match[1];
    const source = citationToken.test(token) ? eligible.get(token) : undefined;
    if (source && previousCitation !== token) {
      parts.push({ type: "source", source });
      previousCitation = token;
    }
    offset = (match.index ?? 0) + match[0].length;
  }
  if (offset < content.length) parts.push({ type: "text", text: content.slice(offset) });
  return parts;
}
