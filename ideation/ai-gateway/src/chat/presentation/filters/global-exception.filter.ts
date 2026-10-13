import { ExceptionFilter, Catch, ArgumentsHost, HttpException, HttpStatus } from '@nestjs/common';
import { Response, Request } from 'express';

@Catch()
export class GlobalExceptionFilter implements ExceptionFilter {
  catch(exception: unknown, host: ArgumentsHost) {
    const ctx = host.switchToHttp();
    const response = ctx.getResponse<Response>();
    const request = ctx.getRequest<Request>();
    const traceId = request.headers['x-request-id'] || 'generated-trace-id';

    let status = HttpStatus.INTERNAL_SERVER_ERROR;
    let message = 'An unexpected internal server error occurred.';
    let errors = [{ code: 'INTERNAL_SERVER_ERROR', detail: 'Please contact support.' }];

    if (exception instanceof HttpException) {
      status = exception.getStatus();
      const res: any = exception.getResponse();
      
      message = res.message || message;
      errors = [{
        code: res.code || 'HTTP_ERROR',
        detail: typeof res.message === 'string' ? res.message : JSON.stringify(res.message)
      }];

      if (res.errors) {
        errors = res.errors; // For Validation Pipe
      }
    }

    response.status(status).json({
      success: false,
      data: null,
      message,
      errors,
      traceId,
      timestamp: new Date().toISOString()
    });
  }
}
