import { Injectable, Logger } from '@nestjs/common';

@Injectable()
export class CircuitBreaker {
  private readonly logger = new Logger(CircuitBreaker.name);
  private failures = new Map<string, number>();
  private readonly THRESHOLD = 3;
  private readonly BACKOFF_MS = 2000;

  async execute<T>(providerId: string, action: () => Promise<T> | AsyncGenerator<T, void, unknown>): Promise<any> {
    const failCount = this.failures.get(providerId) || 0;
    
    if (failCount >= this.THRESHOLD) {
      this.logger.error(`Circuit Open for Provider: ${providerId}. Failing fast.`);
      throw new Error('LLM_PROVIDER_UNAVAILABLE');
    }

    try {
      const result = await action();
      this.failures.set(providerId, 0); // Reset on success
      return result;
    } catch (error: any) {
      this.handleFailure(providerId, error);
    }
  }

  private handleFailure(providerId: string, error: any) {
    const currentFails = (this.failures.get(providerId) || 0) + 1;
    this.failures.set(providerId, currentFails);
    
    if (error?.status === 429) {
      this.logger.warn(`Provider ${providerId} Rate Limited. Fails: ${currentFails}`);
      throw new Error('LLM_PROVIDER_RATE_LIMITED');
    }
    
    throw new Error('LLM_PROVIDER_ERROR');
  }
}
