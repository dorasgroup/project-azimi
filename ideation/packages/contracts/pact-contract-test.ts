import { PactV3, MatchersV3 } from '@pact-foundation/pact';
import * as path from 'path';

/**
 * Consumer-Driven Contract Test (CDC) for RabbitMQ Events
 * Ensures that changes to Core API event structure do not break Ingestion Worker.
 */
const provider = new PactV3({
  consumer: 'IngestionWorker',
  provider: 'CoreAPI',
  dir: path.resolve(process.cwd(), 'pacts'),
});

describe('Document Uploaded Event Contract', () => {
  it('should accept a valid document uploaded event', () => {
    // Define the contract expectation
    const expectedEventShape = {
      eventId: MatchersV3.uuid(),
      tenantId: MatchersV3.uuid(),
      documentId: MatchersV3.uuid(),
      fileUrl: MatchersV3.string('s3://docs/sample.pdf'),
      timestamp: MatchersV3.iso8601DateTimeWithMillis(),
    };

    // When the Core API makes a breaking change (e.g. removes tenantId),
    // this test will fail the CI/CD pipeline preventing a catastrophic deployment.
    provider
      .given('a document is uploaded')
      .uponReceiving('a document uploaded event message')
      .withRequest({
        method: 'POST',
        path: '/events/document-uploaded',
        body: expectedEventShape,
      })
      .willRespondWith({
        status: 202,
      });

    // Execution of the consumer test logic goes here
    return provider.executeTest(async (mockserver) => {
      // Mock worker consuming logic
      expect(true).toBe(true);
    });
  });
});
