import { Controller, Post, Body, Sse, Req, UseGuards, UsePipes, HttpCode } from '@nestjs/common';
import { Observable } from 'rxjs';
import { StreamChatUseCase } from '../application/stream-chat.usecase';
import { InternalChatRequest } from '../domain/internal-chat-request';
import { AuthGuard } from './guards/auth.guard';
import { ZodValidationPipe } from './pipes/zod-validation.pipe';
import { ChatRequestSchema, ChatRequestDto } from './dto/chat-request.dto';

@Controller('api/v1/chat')
export class ChatController {
  constructor(private readonly streamChatUseCase: StreamChatUseCase) {}

  @Post('messages/stream')
  @UseGuards(AuthGuard)
  @UsePipes(new ZodValidationPipe(ChatRequestSchema))
  @Sse()
  streamChat(@Req() req: any, @Body() body: ChatRequestDto): Observable<{ data: any }> {
    const chatRequest: InternalChatRequest = {
      clientMessageId: body.clientMessageId,
      message: body.message,
      userId: req.user.id,
      conversationId: body.conversationId,
      scope: body.scope
    };

    const controller = new AbortController();
    req.on('close', () => controller.abort());

    return new Observable((subscriber) => {
      (async () => {
        try {
          const stream = this.streamChatUseCase.execute(chatRequest, controller.signal);
          for await (const event of stream) {
            subscriber.next({ data: event });
          }
          subscriber.complete();
        } catch (err) {
          // SSE Error Event (Can't use standard HTTP JSON response once stream started)
          subscriber.next({
            data: {
              type: 'stream.failed',
              error: { code: 'AI_STREAM_INTERRUPTED', message: err instanceof Error ? err.message : 'Unknown error' }
            }
          });
          subscriber.complete();
        }
      })();
    });
  }

  @Post('generations/:id/stop')
  @UseGuards(AuthGuard)
  @HttpCode(202)
  async stopGeneration(@Req() req: any) {
    // Application Logic to cancel generation in Orchestrator
    // e.g. await this.stopGenerationUseCase.execute(req.params.id)
    return { success: true, message: 'Generation stop requested' };
  }

  @Post('messages/:id/retry')
  @UseGuards(AuthGuard)
  @HttpCode(200)
  async retryMessage(@Req() req: any) {
    // e.g. await this.retryMessageUseCase.execute(req.params.id)
    return { success: true, message: 'Message retry initiated (Note: return stream or standard response)' };
  }
}
