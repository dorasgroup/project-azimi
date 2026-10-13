import { PipeTransform, Injectable, ArgumentMetadata, BadRequestException } from '@nestjs/common';
import { ZodSchema } from 'zod';

@Injectable()
export class ZodValidationPipe implements PipeTransform {
  constructor(private schema: ZodSchema<any>) {}

  transform(value: any, metadata: ArgumentMetadata) {
    if (metadata.type !== 'body') return value;
    
    const parsedValue = this.schema.safeParse(value);
    if (!parsedValue.success) {
      throw new BadRequestException({
        code: 'CHAT_VALIDATION_FAILED',
        message: 'ورودی نامعتبر است.',
        errors: parsedValue.error.errors
      });
    }
    return parsedValue.data;
  }
}
