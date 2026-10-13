export interface CanonicalCompletionRequest {
  requestId: string;
  modelAlias: string;
  messages: { role: 'system' | 'user' | 'assistant'; content: string }[];
  generation: {
    stream: boolean;
    temperature?: number;
    maximumOutputTokens?: number;
  };
}

export interface CanonicalStreamEvent {
  type: 'generation.started' | 'content.delta' | 'usage.updated' | 'generation.completed' | 'generation.failed';
  delta?: string;
  usage?: { inputTokens: number; outputTokens: number; estimatedCost: number };
  error?: { code: string; message: string };
}

export interface LanguageModelProvider {
  stream(request: CanonicalCompletionRequest): AsyncGenerator<CanonicalStreamEvent, void, unknown>;
  healthCheck(): Promise<boolean>;
}

export interface ModelPolicy {
  policyId: string;
  primaryModelAlias: string;
  fallbackModelAliases: string[];
  maximumInputTokens: number;
}
