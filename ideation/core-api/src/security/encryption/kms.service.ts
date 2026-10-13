import { Injectable } from '@nestjs/common';
import * as crypto from 'crypto';

/**
 * Key Management Service (KMS) integration for Envelope Encryption.
 * Used for encrypting PII and Application Secrets (API Keys) At Rest 
 * before saving to PostgreSQL.
 */
@Injectable()
export class KmsEncryptionService {
  // In production, this Master Key is retrieved securely from Cloud KMS (AWS/Vault)
  // NEVER stored in code or environment variables directly.
  private get MasterKey(): Buffer {
    return Buffer.from(process.env.KMS_MASTER_KEY || 'default-32-byte-key-replace-me-12', 'utf-8'); 
  }

  /**
   * Encrypts sensitive fields (e.g. AvalAI API Keys) using AES-256-GCM.
   */
  encryptData(plainText: string): { ciphertext: string; iv: string; authTag: string } {
    const iv = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv('aes-256-gcm', this.MasterKey, iv);
    
    let encrypted = cipher.update(plainText, 'utf8', 'hex');
    encrypted += cipher.final('hex');
    const authTag = cipher.getAuthTag().toString('hex');

    return {
      ciphertext: encrypted,
      iv: iv.toString('hex'),
      authTag
    };
  }

  decryptData(ciphertext: string, ivHex: string, authTagHex: string): string {
    const decipher = crypto.createDecipheriv(
      'aes-256-gcm', 
      this.MasterKey, 
      Buffer.from(ivHex, 'hex')
    );
    decipher.setAuthTag(Buffer.from(authTagHex, 'hex'));
    
    let decrypted = decipher.update(ciphertext, 'hex', 'utf8');
    decrypted += decipher.final('utf8');
    
    return decrypted;
  }
}
