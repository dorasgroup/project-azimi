import { useRef, useState } from 'react';
import { apiRequest } from '@/lib/api/client';
import { getAccessToken } from '@/lib/auth/session';
import { useChatSessionStore } from './use-chat-session';
import { ChatResponse } from '../types/chat.types';
import { getFriendlyChatError } from '../utils/error-mapper';
import { getChatDisplayContent } from '@/lib/chat/display-content';

export const useChatStream = () => {
  const [isSending, setIsSending] = useState(false);
  const sendingRef = useRef(false);
  const [waitingSessionId, setWaitingSessionId] = useState<string | null>(null);
  const [typingMessageId, setTypingMessageId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  
  const { activeSessionId, sessions, updateSession } = useChatSessionStore();
  const activeSession = sessions.find(s => s.id === activeSessionId) ?? sessions[0];

  const typeAssistantMessage = async (sessionId: string, messageId: string, fullAnswer: string) => {
    setTypingMessageId(messageId);
    if (!fullAnswer) {
      setTypingMessageId(null);
      return;
    }

    const step = Math.max(1, fullAnswer.length > 450 ? 4 : 2);
    let nextIndex = 0;

    await new Promise<void>((resolve) => {
      const timer = window.setInterval(() => {
        nextIndex = Math.min(fullAnswer.length, nextIndex + step);
        const visibleAnswer = fullAnswer.slice(0, nextIndex);

        updateSession(sessionId, (session) => ({
          ...session,
          messages: session.messages.map((item) =>
            item.id === messageId ? { ...item, content: visibleAnswer } : item,
          ),
          updatedAt: new Date().toISOString(),
        }));

        if (getChatDisplayContent(visibleAnswer, true)) {
          setWaitingSessionId((currentId) => (currentId === sessionId ? null : currentId));
        }

        if (nextIndex >= fullAnswer.length) {
          window.clearInterval(timer);
          setTypingMessageId(null);
          resolve();
        }
      }, 18);
    });
  };

  const sendMessage = async (content: string, categoryIds: string[]) => {
    if (sendingRef.current) return;
    if (!activeSession) return;
    const token = getAccessToken();
    if (!token) {
      setError("برای گفتگو ابتدا وارد پنل شوید.");
      return;
    }
    
    if (categoryIds.length === 0) {
      const nextTitle = activeSession.messages.length === 0 ? content.slice(0, 42) : activeSession.title;
      updateSession(activeSession.id, (session) => ({
        ...session,
        title: nextTitle || "چت جدید",
        messages: [
          ...session.messages,
          { id: `user-${Date.now()}`, role: "user", content },
          { id: `assistant-${Date.now()}`, role: "assistant", content: "لطفاً ابتدا مخزن مورد نظر خود را انتخاب کرده و سپس مجدداً سوال خود را بپرسید." },
        ],
        updatedAt: new Date().toISOString(),
      }));
      return;
    }

    const targetSessionId = activeSession.id;
    const targetConversationId = activeSession.conversationId;
    sendingRef.current = true;
    setWaitingSessionId(targetSessionId);
    const nextTitle = activeSession.messages.length === 0 ? content.slice(0, 42) : activeSession.title;
    
    updateSession(targetSessionId, (session) => ({
      ...session,
      title: nextTitle || "چت جدید",
      messages: [...session.messages, { id: `user-${Date.now()}`, role: "user", content }],
      updatedAt: new Date().toISOString(),
    }));
    
    setError(null);
    setIsSending(true);

    try {
      const response = await apiRequest<ChatResponse>("/chat/messages", {
        method: "POST",
        token,
        body: JSON.stringify({
          message: content,
          conversation_id: targetConversationId,
          category_ids: categoryIds,
          inline_citations: true,
        }),
      });

      const assistantMessageId = response.message_id ?? `assistant-${Date.now()}`;

      updateSession(targetSessionId, (session) => ({
        ...session,
        conversationId: response.conversation_id,
        messages: [
          ...session.messages,
          {
            id: assistantMessageId,
            role: "assistant",
            content: "",
            sources: response.sources,
          },
        ],
        updatedAt: new Date().toISOString(),
      }));
      
      await typeAssistantMessage(targetSessionId, assistantMessageId, response.answer);
    } catch (caughtError) {
      setTypingMessageId(null);
      const errorMessage = caughtError instanceof Error ? caughtError.message : "ارسال پیام انجام نشد.";
      setError(getFriendlyChatError(errorMessage));
    } finally {
      setWaitingSessionId((currentId) => (currentId === targetSessionId ? null : currentId));
      sendingRef.current = false;
      setIsSending(false);
    }
  };

  return {
    isSending,
    waitingSessionId,
    typingMessageId,
    error,
    setError,
    sendMessage,
  };
};
