// Example of a Synthetic Monitoring Test (e.g., using Playwright or k6)
import { test, expect } from '@playwright/test';

/**
 * SYNTHETIC MONITORING
 * 
 * Goal: Proactively test the most critical path (Chat Streaming) every 5 minutes.
 * Constraints:
 * 1. Must use a dedicated "Synthetic Tenant".
 * 2. Must not use real user data.
 * 3. Results will be marked with a special label `is_synthetic=true` to not skew real user SLOs.
 */
test('Synthetic Check: Chat Streaming Time to First Token (TTFT)', async ({ request }) => {
  const startTime = Date.now();
  
  const response = await request.post('https://api.internal.ai/v1/chat/completions', {
    headers: {
      'Authorization': `Bearer ${process.env.SYNTHETIC_TEST_TOKEN}`,
      'X-Tenant-ID': 'tenant-synthetic-001'
    },
    data: {
      modelAlias: "chat-fast",
      messages: [{ role: "user", content: "Ping: synthetic health check." }],
      stream: true
    }
  });

  expect(response.ok()).toBeTruthy();

  // Wait for the very first chunk of stream (Time to First Token)
  const stream = await response.body();
  const timeToFirstTokenMs = Date.now() - startTime;

  // Validate SLA condition for Synthetic check
  expect(timeToFirstTokenMs).toBeLessThan(1500); // 1.5s SLA for TTFT
  
  // Note: Actual metric export would be handled by the test runner injecting the 'timeToFirstTokenMs' into Prometheus
});
