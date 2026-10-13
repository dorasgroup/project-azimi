import { Injectable, Logger } from '@nestjs/common';

import * as sanitizeHtml from 'sanitize-html';

@Injectable()
export class AiSecurityService {
  private readonly logger = new Logger(AiSecurityService.name);

  // Blacklisted prompt injection keywords/heuristics (Basic Defense in Depth)
  private readonly PROMPT_INJECTION_HEURISTICS = [
    'ignore previous instructions',
    'system prompt',
    'forget everything',
    'you are now',
    'output your instructions'
  ];

  /**
   * Scans user input before sending it to the model.
   * Logs and blocks severe prompt injection attempts.
   */
  validateUserInput(prompt: string): void {
    const lowerPrompt = prompt.toLowerCase();
    
    for (const heuristic of this.PROMPT_INJECTION_HEURISTICS) {
      if (lowerPrompt.includes(heuristic)) {
        this.logger.warn(`Prompt Injection Attempt Detected! Pattern: ${heuristic}`);
        // Instead of outright blocking, we could sanitize or wrap it. 
        // For strictness, we throw a specific security exception:
        throw new Error('SECURITY_PROMPT_INJECTION_DETECTED');
      }
    }

    // Ensure prompt does not exceed limits (DoS protection)
    if (prompt.length > 4096) {
      throw new Error('SECURITY_RATE_LIMIT_EXCEEDED');
    }
  }

  /**
   * Sanitizes the output from the LLM provider to prevent Cross-Site Scripting (XSS)
   * or malicious markdown execution on the frontend.
   */
  sanitizeModelOutput(rawOutput: string): string {
    // Remove unsafe HTML/Scripts that a hallucinating/compromised model might produce
    return sanitizeHtml(rawOutput, {
      allowedTags: ['b', 'i', 'em', 'strong', 'a', 'p', 'ul', 'ol', 'li', 'code', 'pre', 'h1', 'h2', 'h3', 'br'],
      allowedAttributes: {
        'a': ['href', 'target', 'rel']
      },
      transformTags: {
        'a': sanitizeHtml.simpleTransform('a', { target: '_blank', rel: 'noopener noreferrer' })
      }
    });
  }

  /**
   * Enforces tool calling boundaries (e.g., SSRF protection)
   */
  validateToolCall(toolName: string, argumentsObj: any): void {
    const dangerousTools = ['execute_shell', 'read_env', 'curl'];
    if (dangerousTools.includes(toolName)) {
      this.logger.error(`Unauthorized Tool Call attempt: ${toolName}`);
      throw new Error('SECURITY_TOOL_ACCESS_DENIED');
    }
  }
}
