/**
 * Security Error Taxonomy explicitly defined to standardize 
 * auditing and incident response across the platform.
 */
export const SecurityErrorTaxonomy = {
  SECURITY_AUTHENTICATION_FAILED: { code: 'SECURITY_AUTHENTICATION_FAILED', message: 'Invalid credentials or missing token.' },
  SECURITY_MFA_REQUIRED: { code: 'SECURITY_MFA_REQUIRED', message: 'Multi-factor authentication is required for this action.' },
  SECURITY_TOKEN_EXPIRED: { code: 'SECURITY_TOKEN_EXPIRED', message: 'Authentication token has expired. Please use refresh token.' },
  SECURITY_TOKEN_INVALID: { code: 'SECURITY_TOKEN_INVALID', message: 'Token signature or structure is invalid.' },
  SECURITY_PERMISSION_DENIED: { code: 'SECURITY_PERMISSION_DENIED', message: 'You do not have the required role or permission.' },
  SECURITY_TENANT_CONTEXT_MISSING: { code: 'SECURITY_TENANT_CONTEXT_MISSING', message: 'Cross-tenant isolation enforced. Valid Tenant ID missing.' },
  SECURITY_PROMPT_INJECTION_DETECTED: { code: 'SECURITY_PROMPT_INJECTION_DETECTED', message: 'Malicious intent or prompt injection detected in payload.' },
  SECURITY_TOOL_ACCESS_DENIED: { code: 'SECURITY_TOOL_ACCESS_DENIED', message: 'Attempt to execute unauthorized or dangerous tool.' },
  SECURITY_MALICIOUS_FILE_DETECTED: { code: 'SECURITY_MALICIOUS_FILE_DETECTED', message: 'File rejected by Quarantine / Malware scanner.' },
  SECURITY_RATE_LIMIT_EXCEEDED: { code: 'SECURITY_RATE_LIMIT_EXCEEDED', message: 'Rate limit or resource exhaustion prevention triggered.' },
};
