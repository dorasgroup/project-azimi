import * as request from 'supertest';
import { Test } from '@nestjs/testing';
import { AppModule } from '../../src/app.module';
import { INestApplication } from '@nestjs/common';
import { TestDataFactory } from '../data/test-data-factory';

describe('Tenant Isolation Gate (e2e) - CRITICAL SECURITY GATE', () => {
  let app: INestApplication;
  let tenantA_Token: string;
  let tenantB_Token: string;
  let tenantB_DocumentId: string;

  beforeAll(async () => {
    // 1. App Initialization (using Testcontainers in a real setup)
    const moduleFixture = await Test.createTestingModule({
      imports: [AppModule],
    }).compile();
    app = moduleFixture.createNestApplication();
    await app.init();

    // 2. Data Factory Setup
    const factory = new TestDataFactory();
    
    const tenantA = await factory.createTenant('Tenant_A');
    const tenantB = await factory.createTenant('Tenant_B');

    tenantA_Token = await factory.generateJwtForTenant(tenantA.id);
    tenantB_Token = await factory.generateJwtForTenant(tenantB.id);

    // Tenant B uploads a document
    const docB = await factory.createDocument(tenantB.id, 'Financial_Report_B.pdf');
    tenantB_DocumentId = docB.id;
  });

  it('MUST NOT allow Tenant A to access Tenant B document by guessing the ID (BOLA/IDOR)', async () => {
    const response = await request(app.getHttpServer())
      .get(`/api/documents/${tenantB_DocumentId}`)
      .set('Authorization', `Bearer ${tenantA_Token}`); // Attacker belongs to Tenant A

    // Expecting 404 (Not Found) or 403 (Forbidden). 404 is better to prevent ID discovery.
    expect([404, 403]).toContain(response.status);
  });

  it('MUST NOT return Tenant B documents in Tenant A search queries', async () => {
    const response = await request(app.getHttpServer())
      .get(`/api/documents/search?query=Financial`)
      .set('Authorization', `Bearer ${tenantA_Token}`);

    // Expecting success, but the result array MUST NOT contain Tenant B's doc
    expect(response.status).toBe(200);
    expect(response.body.data.some(doc => doc.id === tenantB_DocumentId)).toBe(false);
  });

  afterAll(async () => {
    await app.close();
  });
});
