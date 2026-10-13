import { Injectable, HttpException, HttpStatus } from '@nestjs/common';
import { ModelPolicy } from './provider.interface';

@Injectable()
export class ModelRegistryService {
  private readonly registry = new Map<string, string>([
    ['chat-primary', 'avalai/gpt-4o'], // Alias to specific provider model
    ['chat-fallback', 'azure/gpt-4'],
    ['embedding-fa', 'avalai/text-embedding-ada-002']
  ]);

  private readonly policies = new Map<string, ModelPolicy>([
    ['rag-chat-standard', {
      policyId: 'rag-chat-standard',
      primaryModelAlias: 'chat-primary',
      fallbackModelAliases: ['chat-fallback'],
      maximumInputTokens: 12000,
    }]
  ]);

  resolveProviderModel(alias: string): string {
    const providerModel = this.registry.get(alias);
    if (!providerModel) {
      throw new HttpException('LLM_MODEL_NOT_FOUND', HttpStatus.BAD_REQUEST);
    }
    return providerModel; // returns e.g. "avalai/gpt-4o"
  }

  getPolicy(policyId: string): ModelPolicy {
    const policy = this.policies.get(policyId);
    if (!policy) {
      throw new HttpException('POLICY_NOT_FOUND', HttpStatus.BAD_REQUEST);
    }
    return policy;
  }
}
