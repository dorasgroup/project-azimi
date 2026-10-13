export interface InternalChatRequest {
  clientMessageId: string;
  message: string;
  userId: string;
  conversationId?: string | null;
  scope?: {
    documentIds?: string[];
    categoryIds?: string[];
  };
}
