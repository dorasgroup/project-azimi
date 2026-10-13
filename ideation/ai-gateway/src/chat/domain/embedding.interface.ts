export interface CanonicalEmbeddingRequest {
  requestId: string;
  correlationId: string;
  modelPolicy: {
    policyId: string;
    modelAlias: string;
  };
  inputs: {
    id: string;
    content: string;
    contentHash: string;
  }[];
  options?: {
    inputType: 'document' | 'query';
    normalize?: boolean;
    truncate?: boolean;
  };
}

export interface CanonicalEmbeddingResponse {
  modelAlias: string;
  embeddingVersion: string;
  vectorDimension: number;
  vectors: {
    id: string;
    vector: number[];
    tokenCount: number;
  }[];
  usage: {
    inputTokens: number;
  };
  cost: {
    estimated: number;
    currency: string;
  };
}

export interface EmbeddingProvider {
  embedDocuments(request: CanonicalEmbeddingRequest): Promise<CanonicalEmbeddingResponse>;
  embedQuery(request: CanonicalEmbeddingRequest): Promise<CanonicalEmbeddingResponse>;
  healthCheck(): Promise<boolean>;
}
