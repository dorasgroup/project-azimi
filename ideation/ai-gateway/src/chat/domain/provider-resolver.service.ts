import { Injectable, HttpException, HttpStatus } from '@nestjs/common';
import { ModelRegistryService } from './model-registry.service';

@Injectable()
export class ProviderResolverService {
  constructor(private readonly registry: ModelRegistryService) {}

  /**
   * Resolves the correct Provider and Model purely based on Policy,
   * Fallback rules, and Circuit Breaker states, completely agnostic of name.
   */
  resolvePrimary(policyId: string): string {
    const policy = this.registry.getPolicy(policyId);
    return policy.primaryModelAlias;
  }

  resolveFallback(policyId: string, currentFailedAlias: string): string {
    const policy = this.registry.getPolicy(policyId);
    
    // Find the next available fallback model in the chain
    const fallback = policy.fallbackModelAliases.find(alias => alias !== currentFailedAlias);
    
    if (!fallback) {
      throw new HttpException('LLM_FAILOVER_NOT_AVAILABLE', HttpStatus.SERVICE_UNAVAILABLE);
    }
    
    return fallback;
  }
}
