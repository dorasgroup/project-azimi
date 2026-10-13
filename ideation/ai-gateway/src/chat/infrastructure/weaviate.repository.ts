import { Injectable, Logger, HttpException, HttpStatus } from '@nestjs/common';
import weaviate, { WeaviateClient } from 'weaviate-ts-client';

export interface CanonicalSearchRequest {
  query: string;
  tenantId: string;
  allowedDocumentIds?: string[];
  embeddingVersion: string;
  topK: number;
}

@Injectable()
export class WeaviateRepository {
  private readonly logger = new Logger(WeaviateRepository.name);
  private client: WeaviateClient;

  constructor() {
    this.client = weaviate.client({
      scheme: 'http',
      host: process.env.WEAVIATE_HOST || 'weaviate:8080',
      apiKey: new weaviate.ApiKey(process.env.WEAVIATE_API_KEY || 'your_secure_weaviate_key'),
    });
  }

  /**
   * Search via Hybrid Strategy (Vector + Keyword)
   * GUARANTEES Tenant Isolation using Where filters.
   */
  async search(request: CanonicalSearchRequest): Promise<any[]> {
    this.logger.log(`Executing Hybrid Search for Tenant [${request.tenantId}]`);

    // Strict Multi-tenancy Guard
    let whereFilter: any = {
      path: ['tenantId'],
      operator: 'Equal',
      valueText: request.tenantId,
    };

    // Apply allowed documents filter if specified
    if (request.allowedDocumentIds && request.allowedDocumentIds.length > 0) {
      whereFilter = {
        operator: 'And',
        operands: [
          whereFilter,
          {
            path: ['publicDocumentId'],
            operator: 'ContainsAny',
            valueTextArray: request.allowedDocumentIds,
          }
        ]
      };
    }

    try {
      const res = await this.client.graphql
        .get()
        .withClassName('DocumentChunk')
        .withFields('publicDocumentId content pageStart pageEnd _additional { score distance id }')
        .withHybrid({
          query: request.query,
          alpha: 0.5, // 50% Vector, 50% Keyword (BM25)
        })
        .withWhere(whereFilter) // TENANT GUARD APPLIED HERE
        .withLimit(request.topK || 5)
        .do();

      return res.data.Get.DocumentChunk || [];
    } catch (error: any) {
      this.logger.error(`Weaviate Search Failed: ${error.message}`);
      throw new HttpException('RETRIEVAL_UNAVAILABLE', HttpStatus.SERVICE_UNAVAILABLE);
    }
  }
}
