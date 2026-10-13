import { z } from 'zod';

export const ChatRequestSchema = z.object({
  clientMessageId: z.string().uuid({ message: 'Invalid Message ID format' }),
  message: z.string().min(1).max(4000, { message: 'Message exceeds length limit' }),
  conversationId: z.string().uuid().nullable().optional(),
  scope: z.object({
    documentIds: z.array(z.string().uuid()).optional(),
    categoryIds: z.array(z.string().uuid()).optional(),
  }).optional(),
  options: z.object({
    language: z.string().max(10).optional(),
    responseFormat: z.enum(['markdown', 'text', 'json']).optional()
  }).optional()
});

export type ChatRequestDto = z.infer<typeof ChatRequestSchema>;
