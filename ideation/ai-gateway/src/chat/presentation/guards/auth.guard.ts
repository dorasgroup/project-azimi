import { Injectable, CanActivate, ExecutionContext, UnauthorizedException } from '@nestjs/common';
import { Observable } from 'rxjs';

@Injectable()
export class AuthGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean | Promise<boolean> | Observable<boolean> {
    const request = context.switchToHttp().getRequest();
    const authHeader = request.headers.authorization;
    
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      throw new UnauthorizedException({
        code: 'CHAT_UNAUTHORIZED',
        message: 'عدم احراز هویت. لطفاً وارد شوید.'
      });
    }

    // Mock Token Verification (In real app, verify with JWKS)
    const token = authHeader.split(' ')[1];
    if (token === 'invalid_token') {
      throw new UnauthorizedException({
        code: 'CHAT_UNAUTHORIZED',
        message: 'توکن نامعتبر است.'
      });
    }

    // Set User Context
    request.user = {
      id: 'mock-user-id',
      tenantId: 'mock-tenant-id'
    };

    return true;
  }
}
