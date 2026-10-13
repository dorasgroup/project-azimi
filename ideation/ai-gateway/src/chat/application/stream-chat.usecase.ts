import { Injectable, Inject } from '@nestjs/common';
import { AI_ORCHESTRATOR, AIOrchestrator } from '../domain/ai-orchestrator.interface';
import { InternalChatRequest } from '../domain/internal-chat-request';
import { AIStreamEvent } from '../domain/ai-stream-event';

@Injectable()
export class StreamChatUseCase {
  constructor(
    @Inject(AI_ORCHESTRATOR) private readonly orchestrator: AIOrchestrator,
  ) {}

  async *execute(request: InternalChatRequest, signal: AbortSignal): AsyncIterable<AIStreamEvent> {
    // 1. Quota Check (Mocked)
    // 2. Validation (Mocked)
    
    // 3. Delegate to Orchestrator
    const stream = this.orchestrator.streamChat(request, signal);

    for await (const event of stream) {
      // Here we could sanitize or log token usage
      yield event;
    }
  }
}
