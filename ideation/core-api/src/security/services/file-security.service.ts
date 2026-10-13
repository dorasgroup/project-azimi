import { Injectable, BadRequestException } from '@nestjs/common';
import { SecurityErrorTaxonomy } from '../exceptions/security-error-taxonomy';
import * as fileType from 'file-type';

@Injectable()
export class FileSecurityService {
  private readonly MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB
  
  // Safe Mime Types
  private readonly ALLOWED_MIME_TYPES = [
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'text/plain',
    'text/markdown'
  ];

  /**
   * Evaluates file safety before passing it to Quarantine or Parsing layer.
   * Trust NO extension. Check "Magic Numbers" of the file.
   */
  async validateFile(buffer: Buffer, originalExtension: string): Promise<void> {
    // 1. Size Limit
    if (buffer.length > this.MAX_FILE_SIZE) {
      throw new BadRequestException(SecurityErrorTaxonomy.SECURITY_RATE_LIMIT_EXCEEDED);
    }

    // 2. Magic Number Inspection (Do not trust the client extension)
    const typeInfo = await fileType.fromBuffer(buffer);
    if (!typeInfo || !this.ALLOWED_MIME_TYPES.includes(typeInfo.mime)) {
      throw new BadRequestException(SecurityErrorTaxonomy.SECURITY_MALICIOUS_FILE_DETECTED);
    }

    // 3. Zip Bomb Detection (Decompression Ratio Estimation)
    // If it's a docx (which is a zip archive under the hood), check compression ratio
    if (typeInfo.mime === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document') {
       const isZipBomb = this.detectZipBombHeuristic(buffer);
       if (isZipBomb) {
         throw new BadRequestException(SecurityErrorTaxonomy.SECURITY_ZIP_BOMB_DETECTED);
       }
    }
  }

  /**
   * Basic heuristic: If the raw size is extremely small but the declared 
   * uncompressed size in central directory is massive, it's a zip bomb.
   */
  private detectZipBombHeuristic(buffer: Buffer): boolean {
    // In a real implementation, we stream the central directory headers
    // and check if decompressed_size / compressed_size > 1000.
    // Stub implementation:
    return false;
  }
}
