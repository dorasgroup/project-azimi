import { Injectable, Logger } from '@nestjs/common';
import { LanguageModelProvider, CanonicalCompletionRequest, CanonicalStreamEvent } from '../../domain/provider.interface';
import { CircuitBreaker } from '../../domain/circuit-breaker';
// Note: In real implementation, axios or node-fetch is used.
import axios from 'axios';

@Injectable()
export class AvalAIAdapter implements LanguageModelProvider {
  private readonly logger = new Logger(AvalAIAdapter.name);
  private readonly baseUrl = process.env.AVALAI_BASE_URL || 'https://api.avalai.ir/v1';
  private readonly apiKey = process.env.PROVIDER_API_KEY || ''; // Generic key name

  constructor(private readonly circuitBreaker: CircuitBreaker) {}

  async *stream(request: CanonicalCompletionRequest): AsyncGenerator<CanonicalStreamEvent, void, unknown> {
    // 1. Resolve to AvalAI specific model ID
    const providerModelId = request.modelAlias.split('/')[1] || request.modelAlias;
    
    this.logger.log(`Routing canonical request to AvalAI [Model: ${providerModelId}]`);

    // 2. Prepare Provider-specific payload (OpenAI-compatible)
    const payload = {
      model: providerModelId,
      messages: request.messages,
      stream: request.generation.stream,
      temperature: request.generation.temperature || 0.7,
      max_tokens: request.generation.maximumOutputTokens || 1000
    };

    yield { type: 'generation.started' };

    try {
      // 3. Execution guarded by Circuit Breaker
      yield* await this.circuitBreaker.execute('avalai', async function*() {
        // Mocking the SSE stream response from Axios
        const response = await axios.post(`${this.baseUrl}/chat/completions`, payload, {
          headers: { 'Authorization': `Bearer ${this.apiKey}` },
          responseType: 'stream'
        });

        for await (const chunk of response.data) {
          const textChunk = chunk.toString();
          if (textChunk.includes('[DONE]')) break;
          // Mapping provider raw chunk to Canonical Delta
          yield { type: 'content.delta', delta: "mock-delta-content" } as CanonicalStreamEvent;
        }
      }.bind(this)());

      yield { type: 'usage.updated', usage: { inputTokens: 50, outputTokens: 100, estimatedCost: 0.002 } };
      yield { type: 'generation.completed' };

    } catch (error: any) {
      this.logger.error(`AvalAI Request Failed: ${error.message}`);
      // Mapping raw errors to canonical Error Taxonomy
      let canonicalErrorCode = 'LLM_PROVIDER_UNAVAILABLE';
      if (error.message === 'LLM_PROVIDER_RATE_LIMITED') canonicalErrorCode = 'LLM_PROVIDER_RATE_LIMITED';
      
      yield { type: 'generation.failed', error: { code: canonicalErrorCode, message: 'Generation interrupted' } };
    }
  }

  async healthCheck(): Promise<boolean> {
    try {
      await axios.get(`${this.baseUrl}/models`, { headers: { 'Authorization': `Bearer ${this.apiKey}` } });
      return true;
    } catch {
      return false;
    }
  }
}
