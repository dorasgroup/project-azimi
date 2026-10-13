import { InternalChatRequest } from './internal-chat-request';
import { AIStreamEvent } from './ai-stream-event';

export const AI_ORCHESTRATOR = 'AI_ORCHESTRATOR';

export interface AIOrchestrator {
  streamChat(request: InternalChatRequest, signal: AbortSignal): AsyncIterable<AIStreamEvent>;
}
