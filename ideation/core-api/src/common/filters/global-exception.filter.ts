import { ExceptionFilter, Catch, ArgumentsHost, HttpException, HttpStatus } from '@nestjs/common';
import { Request, Response } from 'express';
import { v4 as uuidv4 } from 'uuid';

/**
 * Canonical API Error Model
 * As defined in Prompt 15, raw errors (Postgres, Dify, Weaviate) MUST NEVER leak to the client.
 */
@Catch()
export class GlobalExceptionFilter implements ExceptionFilter {
  catch(exception: unknown, host: ArgumentsHost) {
    const ctx = host.switchToHttp();
    const response = ctx.getResponse<Response>();
    const request = ctx.getRequest<Request>();
    
    // Default to Internal Server Error
    let status = HttpStatus.INTERNAL_SERVER_ERROR;
    let code = 'INTERNAL_ERROR';
    let message = 'An unexpected error occurred.';
    let details = [];

    // Map recognized HTTP Exceptions
    if (exception instanceof HttpException) {
      status = exception.getStatus();
      const res = exception.getResponse() as any;
      message = res.message || exception.message;
      code = this.mapStatusToErrorCode(status);
      details = res.details || [];
    } else {
      // Log the raw exception internally, but NEVER leak it!
      console.error('[CRITICAL] Unhandled Exception:', exception);
    }

    // Standardized Canonical Error Response
    response.status(status).json({
      error: {
        code: code,
        message: message,
        requestId: request.headers['x-request-id'] || uuidv4(),
        details: details
      }
    });
  }

  private mapStatusToErrorCode(status: number): string {
    switch (status) {
      case 400: return 'VALIDATION_ERROR';
      case 401: return 'UNAUTHORIZED';
      case 403: return 'FORBIDDEN';
      case 404: return 'RESOURCE_NOT_FOUND';
      case 429: return 'RATE_LIMIT_EXCEEDED';
      case 503: return 'DEPENDENCY_UNAVAILABLE';
      default: return 'INTERNAL_ERROR';
    }
  }
}
