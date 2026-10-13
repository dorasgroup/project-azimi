"use client";

import { ExternalLink } from "lucide-react";
import { Children, cloneElement, createContext, isValidElement, useContext, useId, useMemo, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import type { ChatSource } from "@/features/chat/types/chat.types";
import { getChatDisplayContent } from "@/lib/chat/display-content";
import { getAssistantMarkdown } from "@/lib/chat/markdown";
import { getAnswerParts } from "@/lib/chat/sources";
import type { ArticleCitation } from "@/lib/chat/sources";
import { SourcePopup } from "@/components/upload/source-popup";

type PreparedAnswer = { markdown: string; citations: ArticleCitation[]; prefix: string };
type CitationContextValue = {
  answer: PreparedAnswer;
  popupId: string;
  onSelect: (source: ArticleCitation) => void;
};
const CitationContext = createContext<CitationContextValue | null>(null);

function renderCitationText(children: ReactNode, context: CitationContextValue): ReactNode {
  const { answer, popupId, onSelect } = context;
  return Children.map(children, (child) => {
    if (typeof child === "string") {
      const nodes: ReactNode[] = [];
      const markers = new RegExp(`${answer.prefix}(\\d+)\uE001`, "g");
      let offset = 0;
      for (const match of child.matchAll(markers)) {
        nodes.push(child.slice(offset, match.index));
        const index = Number(match[1]);
        const source = answer.citations[index];
        if (source) nodes.push(
          <button className="chat-source-chip" data-citation-id={source.id} key={`${source.id}-${index}`}
            type="button" aria-haspopup="dialog" aria-controls={popupId}
            aria-label={`نمایش منبع: ${source.title}`} onClick={() => onSelect(source)}>
            منبع
          </button>,
        );
        offset = match.index + match[0].length;
      }
      nodes.push(child.slice(offset));
      return nodes;
    }
    if (isValidElement<{ children?: ReactNode }>(child) && child.props.children) {
      return cloneElement(child, { children: renderCitationText(child.props.children, context) });
    }
    return child;
  });
}

function CitationText({ children }: { children?: ReactNode }) {
  const context = useContext(CitationContext);
  return <>{context ? renderCitationText(children, context) : children}</>;
}

// Stable component identities keep the existing paragraph/chip DOM mounted
// when source selection changes or the answer continues typing.
const markdownComponents: Components = {
  p: ({ children }) => <p><CitationText>{children}</CitationText></p>,
  h1: ({ children }) => <h1><CitationText>{children}</CitationText></h1>,
  h2: ({ children }) => <h2><CitationText>{children}</CitationText></h2>,
  h3: ({ children }) => <h3><CitationText>{children}</CitationText></h3>,
  h4: ({ children }) => <h4><CitationText>{children}</CitationText></h4>,
  h5: ({ children }) => <h5><CitationText>{children}</CitationText></h5>,
  h6: ({ children }) => <h6><CitationText>{children}</CitationText></h6>,
  li: ({ children }) => <li><CitationText>{children}</CitationText></li>,
  code: ({ children }) => <code><CitationText>{children}</CitationText></code>,
  // Model links/images are inert labels; only the validated source popup
  // retains an interactive reference to a stored article URL.
  a: ({ children }) => <CitationText>{children}</CitationText>,
  img: ({ alt }) => <CitationText>{alt ?? ""}</CitationText>,
};
const allowedMarkdownElements = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "strong", "em", "blockquote", "br", "hr", "pre", "code", "a", "img"];

export function AssistantResponse({ content, sources, isTyping = false }: {
  content: string;
  sources?: ChatSource[];
  isTyping?: boolean;
}) {
  const [selectedSource, setSelectedSource] = useState<ArticleCitation | null>(null);
  const id = useId();
  const popupId = `chat-source-${id.replace(/:/g, "")}`;
  const answer = useMemo(() => {
    const parts = getAnswerParts(getChatDisplayContent(content, isTyping, {
      preserveSourceMarkers: true, preserveFormatting: true,
    }), sources);
    const citations: ArticleCitation[] = [];
    // Only prevalidated source parts can create chips. The model cannot forge
    // our placeholders because the chosen prefix is absent from its input.
    let seed = 0;
    while (content.includes(`\uE000chat-citation-${seed}-`)) seed += 1;
    const prefix = `\uE000chat-citation-${seed}-`;
    const markdown = parts.map((part) => {
      if (part.type === "text") return part.text;
      const index = citations.push(part.source) - 1;
      return `${prefix}${index}\uE001`;
    }).join("");
    return { markdown: getAssistantMarkdown(markdown, isTyping), citations, prefix };
  }, [content, sources, isTyping]);

  const citationContext = useMemo(() => ({ answer, popupId, onSelect: setSelectedSource }), [answer, popupId]);

  return (
    <>
      <div className="assistant-response-content">
        <CitationContext.Provider value={citationContext}>
          <ReactMarkdown skipHtml allowedElements={allowedMarkdownElements} components={markdownComponents}>
            {answer.markdown}
          </ReactMarkdown>
        </CitationContext.Provider>
      </div>
      {selectedSource ? (
        <SourcePopup open onClose={() => setSelectedSource(null)} title="منبع پاسخ" id={popupId} className="chat-source-popup"
          footer={(
            <a className="chat-source-original-link" href={selectedSource.url} target="_blank" rel="noopener noreferrer">
              مشاهده مقاله در سایت حامین یار <ExternalLink size={16} aria-hidden="true" />
            </a>
          )}>
          <span className="chat-source-site">حامین یار</span>
          <h3 className="chat-source-title">{selectedSource.title}</h3>
          <span className="chat-source-url" dir="ltr">{selectedSource.url}</span>
          {selectedSource.excerpt ? (
            <section className="chat-source-excerpt" aria-label="بخش مرتبط از مقاله">
              <h4>بخش مرتبط از مقاله</h4>
              <p>{selectedSource.excerpt}</p>
            </section>
          ) : null}
        </SourcePopup>
      ) : null}
    </>
  );
}
