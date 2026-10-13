import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { ChatSession, ChatMessage } from '../types/chat.types';

interface ChatSessionState {
  sessions: ChatSession[];
  activeSessionId: string | null;
  setActiveSessionId: (id: string | null) => void;
  addSession: () => void;
  deleteSession: (id: string) => void;
  updateSession: (id: string, updater: (session: ChatSession) => ChatSession) => void;
}

const createSession = (): ChatSession => ({
  id: `chat-${Date.now()}`,
  title: "چت جدید",
  conversationId: null,
  messages: [],
  updatedAt: new Date().toISOString(),
});

export const useChatSessionStore = create<ChatSessionState>()(
  persist(
    (set) => ({
      sessions: [createSession()],
      activeSessionId: null,
      
      setActiveSessionId: (id) => set({ activeSessionId: id }),
      
      addSession: () => set((state) => {
        const nextSession = createSession();
        return {
          sessions: [nextSession, ...state.sessions],
          activeSessionId: nextSession.id
        };
      }),

      deleteSession: (id) => set((state) => {
        const nextSessions = state.sessions.filter(s => s.id !== id);
        if (nextSessions.length === 0) {
          const fresh = createSession();
          return { sessions: [fresh], activeSessionId: fresh.id };
        }
        return {
          sessions: nextSessions,
          activeSessionId: state.activeSessionId === id ? nextSessions[0].id : state.activeSessionId
        };
      }),

      updateSession: (id, updater) => set((state) => ({
        sessions: state.sessions.map((session) => 
          session.id === id ? updater(session) : session
        )
      })),
    }),
    {
      name: 'ideation_chat_sessions_v2', // New key to avoid conflicts during refactor
      partialize: (state) => ({
        sessions: state.sessions.filter(s => s.messages.length > 0)
      })
    }
  )
);
