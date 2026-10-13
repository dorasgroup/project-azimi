export interface CanonicalSearchRequest {
  query: string;
  tenantContext: {
    tenantId: string;
    accessScopes: string[];
  };
  filters: {
    documentIds?: string[];
    categoryIds?: string[];
    language?: string;
    deleted?: boolean;
  };
  retrieval: {
    topK: number;
    minimumScore: number;
    embeddingVersion: string;
  };
}

export interface CanonicalSearchResult {
  results: {
    chunkId: string;
    publicDocumentId: string;
    content: string;
    score: number;
    distance: number;
    pageStart?: number;
    pageEnd?: number;
    heading?: string;
    metadata?: any;
  }[];
  searchMetadata: {
    totalCandidates: number;
    durationMs: number;
    embeddingVersion: string;
    indexVersion: string;
  };
}
