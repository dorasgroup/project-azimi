import { Injectable, CanActivate, ExecutionContext, UnauthorizedException, ForbiddenException } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { JwtService } from '@nestjs/jwt';
import { SecurityErrorTaxonomy } from '../exceptions/security-error-taxonomy';

/**
 * Deny by Default: If this guard is applied globally, 
 * every route requires authentication and specific permissions.
 */
@Injectable()
export class JwtTenantAuthGuard implements CanActivate {
  constructor(
    private jwtService: JwtService,
    private reflector: Reflector
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const isPublic = this.reflector.get<boolean>('isPublic', context.getHandler());
    if (isPublic) return true;

    const request = context.switchToHttp().getRequest();
    const token = this.extractTokenFromHeader(request);

    if (!token) {
      throw new UnauthorizedException(SecurityErrorTaxonomy.SECURITY_AUTHENTICATION_FAILED);
    }

    try {
      const payload = await this.jwtService.verifyAsync(token);
      
      // Tenant Isolation Enforcement
      // Tenant parameter must NEVER come from body/query. Must be from Token.
      if (!payload.tenant_id) {
        throw new ForbiddenException(SecurityErrorTaxonomy.SECURITY_TENANT_CONTEXT_MISSING);
      }
      
      // MFA Check for Admins
      if (payload.roles.includes('ADMIN') && !payload.mfa_verified) {
        throw new ForbiddenException(SecurityErrorTaxonomy.SECURITY_MFA_REQUIRED);
      }

      request['user'] = payload;
      request['tenant_id'] = payload.tenant_id; // Safe Tenant ID extraction
    } catch (e: any) {
      if (e?.name === 'TokenExpiredError') {
        throw new UnauthorizedException(SecurityErrorTaxonomy.SECURITY_TOKEN_EXPIRED);
      }
      throw new UnauthorizedException(SecurityErrorTaxonomy.SECURITY_TOKEN_INVALID);
    }

    // Role Validation
    const requiredPermissions = this.reflector.get<string[]>('permissions', context.getHandler());
    if (requiredPermissions && requiredPermissions.length > 0) {
      const userPermissions = request.user.permissions || [];
      const hasPermission = requiredPermissions.every(p => userPermissions.includes(p));
      
      if (!hasPermission) {
        throw new ForbiddenException(SecurityErrorTaxonomy.SECURITY_PERMISSION_DENIED);
      }
    }

    return true;
  }

  private extractTokenFromHeader(request: any): string | undefined {
    const [type, token] = request.headers.authorization?.split(' ') ?? [];
    return type === 'Bearer' ? token : undefined;
  }
}
