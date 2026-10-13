export type AIStreamEvent =
  | { type: 'stream.started'; generationId: string; conversationId?: string }
  | { type: 'message.delta'; delta: string }
  | { type: 'citation.added'; citation: any }
  | { type: 'usage.updated'; usage: any }
  | { type: 'stream.completed'; finishReason: string }
  | { type: 'stream.failed'; error: { code: string; message: string } };
