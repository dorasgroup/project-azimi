import { Module } from '@nestjs/common';
import { ChatController } from './presentation/chat.controller';
import { StreamChatUseCase } from './application/stream-chat.usecase';
import { DifyAdapter } from './infrastructure/dify.adapter';
import { CircuitBreaker } from './domain/circuit-breaker';
import { GapGPTAdapter } from './infrastructure/providers/gapgpt.adapter';
import { AI_ORCHESTRATOR } from './domain/ai-orchestrator.interface';

@Module({
  controllers: [ChatController],
  providers: [
    StreamChatUseCase,
    {
      provide: AI_ORCHESTRATOR,
      useClass: DifyAdapter,
    },
    GapGPTAdapter,
    CircuitBreaker,
  ],
})
export class ChatModule {}
