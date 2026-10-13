export type ChatSource = {
  document_name: string | null;
  content: string | null;
  score: number | null;
  document_id: string | null;
  citation_id?: string | null;
  source_type?: string | null;
  source_url?: string | null;
  article_title?: string | null;
};

export type ChatResponse = {
  id: string | null;
  conversation_id: string | null;
  message_id: string | null;
  answer: string;
  sources: ChatSource[];
  created_at: string | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
};

export type ChatSession = {
  id: string;
  title: string;
  conversationId: string | null;
  messages: ChatMessage[];
  updatedAt: string;
  categoryIds?: string[];
};
