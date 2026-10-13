import { Injectable, Logger } from '@nestjs/common';

@Injectable()
export class WorkflowResolverService {
  private readonly logger = new Logger(WorkflowResolverService.name);

  // In a real app, this would be fetched from a DB or Redis cache
  private readonly workflowRegistry = {
    'rag-chat-fa-v1': {
      difyWorkflowId: 'dify-workflow-id-12345',
      environment: 'production',
      features: ['rag-chat'],
      language: 'fa',
      primaryModel: 'avalai/text-embedding-ada-002',
    },
    'rag-chat-fa-v2-canary': {
      difyWorkflowId: 'dify-workflow-id-67890',
      environment: 'production',
      features: ['rag-chat'],
      language: 'fa',
      primaryModel: 'avalai/gpt-4o',
    }
  };

  resolve(tenantId: string, features: string[], language: string): string {
    this.logger.log(`Resolving workflow for Tenant: ${tenantId}, Features: ${features.join(',')}, Lang: ${language}`);
    
    // Simple feature flag / A-B routing logic
    if (tenantId === 'tenant-beta-tester') {
       return this.workflowRegistry['rag-chat-fa-v2-canary'].difyWorkflowId;
    }

    return this.workflowRegistry['rag-chat-fa-v1'].difyWorkflowId;
  }
}
