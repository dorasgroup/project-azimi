"use client";

import { Trash2, Lightbulb } from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useState, useRef } from "react";

import { apiRequest } from "@/lib/api/client";
import { getAccessToken } from "@/lib/auth/session";
import { getChatDisplayContent } from "@/lib/chat/display-content";
import { ChatPreparingStatus } from "./chat-preparing-status";
import { AssistantResponse } from "./assistant-response";
import type { ChatSource } from "@/features/chat/types/chat.types";

type ChatResponse = {
  id: string | null;
  conversation_id: string | null;
  message_id: string | null;
  answer: string;
  sources: ChatSource[];
  created_at: string | null;
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
};

type ChatSession = {
  id: string;
  title: string;
  conversationId: string | null;
  messages: ChatMessage[];
  updatedAt: string;
  categoryIds?: string[];
};

type ChatHistoryResponse = {
  items: Array<{
    session_id: string;
    title: string;
    conversation_id: string | null;
    messages: ChatMessage[];
    updated_at: string;
    category_ids: string[];
  }>;
};

function createSession(): ChatSession {
  const now = new Date().toISOString();
  return {
    id: `chat-${Date.now()}`,
    title: "چت جدید",
    conversationId: null,
    messages: [],
    updatedAt: now,
  };
}

function getFriendlyChatError(message: string) {
  const normalizedMessage = message.toLowerCase();

  if (
    normalizedMessage.includes("quota_exceeded") ||
    normalizedMessage.includes("status code 429") ||
    normalizedMessage.includes("exceeded your current quota") ||
    normalizedMessage.includes("ava.al/billing")
  ) {
    return "درخواست به Dify و مدل AvalAI رسیده، اما کلید API فعلی AvalAI سهمیه یا اعتبار کافی ندارد. اعتبار پنل AvalAI را بررسی یا یک API Key معتبر با شارژ کافی ثبت کنید.";
  }

  if (
    normalizedMessage.includes("api.avalai.ir") &&
    (normalizedMessage.includes("timed out") || normalizedMessage.includes("timeout"))
  ) {
    return "درخواست به Dify رسیده، اما Dify هنگام اتصال به AvalAI با timeout روبه‌رو شده است. اتصال اینترنت، دسترسی به api.avalai.ir و وضعیت مدل را بررسی کنید و دوباره تلاش کنید.";
  }

  if (
    normalizedMessage.includes("openrouter.ai") ||
    normalizedMessage.includes("network is unreachable") ||
    normalizedMessage.includes("timed out") ||
    normalizedMessage.includes("timeout")
  ) {
    return "درخواست به Dify رسیده، اما سرویس مدل هنگام پاسخ‌دهی timeout داده است. اتصال مدل را بررسی کنید و دوباره تلاش کنید.";
  }

  return message;
}

type CategoryItem = {
  id: string;
  name: string;
};

export function ChatPanel() {
  const [message, setMessage] = useState("");
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [pendingDeleteSessionId, setPendingDeleteSessionId] = useState<string | null>(null);
  const [isMobileHistoryOpen, setIsMobileHistoryOpen] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const sendingRef = useRef(false);
  const [waitingSessionId, setWaitingSessionId] = useState<string | null>(null);
  const [typingMessageId, setTypingMessageId] = useState<string | null>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [categories, setCategories] = useState<CategoryItem[]>([]);
  const [selectedCategoryIds, setSelectedCategoryIds] = useState<string[]>([]);
  const [isCategoryModalOpen, setIsCategoryModalOpen] = useState(false);

  async function loadCategories() {
    const token = getAccessToken();
    if (!token) return;
    try {
      const response = await apiRequest<{ items: CategoryItem[] }>("/categories", { token });
      setCategories(response.items || []);
    } catch (err) {
      console.error("Failed to load categories:", err);
    }
  }

  async function loadChatHistory() {
    const token = getAccessToken();
    const draftSession = createSession();
    if (!token) {
      setSessions([draftSession]);
      setActiveSessionId(draftSession.id);
      return;
    }

    try {
      const response = await apiRequest<ChatHistoryResponse>("/chat/history?inline_citations=true", { token });
      const historySessions = response.items.map((item) => ({
        id: item.session_id,
        title: item.title,
        conversationId: item.conversation_id,
        messages: item.messages,
        updatedAt: item.updated_at,
        categoryIds: item.category_ids,
      }));
      setSessions([draftSession, ...historySessions]);
      setActiveSessionId(draftSession.id);
    } catch (err) {
      console.error("Failed to load chat history:", err);
      setSessions([draftSession]);
      setActiveSessionId(draftSession.id);
    }
  }

  useEffect(() => {
    void loadChatHistory();
    void loadCategories();
  }, []);

  useEffect(() => {
    const session = sessions.find((s) => s.id === activeSessionId);
    if (session) {
      setSelectedCategoryIds(session.categoryIds || []);
    }
  }, [activeSessionId]);

  const visibleSessions = useMemo(
    () => sessions.filter((session) => session.messages.length > 0),
    [sessions],
  );

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) ?? sessions[0],
    [activeSessionId, sessions],
  );

  const pendingDeleteSession = useMemo(
    () => sessions.find((session) => session.id === pendingDeleteSessionId) ?? null,
    [pendingDeleteSessionId, sessions],
  );

  const isWaitingForAnswer = waitingSessionId !== null && waitingSessionId === activeSession?.id;

  function updateSessionById(sessionId: string, updater: (session: ChatSession) => ChatSession) {
    setSessions((currentSessions) =>
      currentSessions.map((session) => (session.id === sessionId ? updater(session) : session)),
    );
  }

  async function handleCopyMessage(content: string, messageId: string) {
    if (!content) {
      return;
    }

    try {
      if (!navigator.clipboard || !window.isSecureContext) {
        throw new Error("Clipboard API unavailable");
      }

      await navigator.clipboard.writeText(content);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = content;
      textarea.setAttribute("readonly", "");
      textarea.style.left = "-9999px";
      textarea.style.opacity = "0";
      textarea.style.position = "fixed";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    }

    setCopiedMessageId(messageId);
    window.setTimeout(() => {
      setCopiedMessageId((currentMessageId) => (currentMessageId === messageId ? null : currentMessageId));
    }, 1400);
  }

  async function typeAssistantMessage(sessionId: string, messageId: string, fullAnswer: string) {
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

        updateSessionById(sessionId, (session) => ({
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
  }

  function handleNewChat() {
    if (activeSession && activeSession.messages.length === 0) {
      setIsMobileHistoryOpen(false);
      setMessage("");
      setError(null);
      return;
    }

    const nextSession = createSession();
    setSessions((currentSessions) => [nextSession, ...currentSessions]);
    setActiveSessionId(nextSession.id);
    setIsMobileHistoryOpen(false);
    setMessage("");
    setError(null);
  }

  async function handleDeleteSession(sessionId: string) {
    const token = getAccessToken();
    if (!token) {
      return;
    }

    try {
      await apiRequest<void>(`/chat/history/${encodeURIComponent(sessionId)}`, {
        method: "DELETE",
        token,
      });
    } catch (caughtError) {
      const errorMessage = caughtError instanceof Error ? caughtError.message : "Ø­Ø°Ù Ú†Øª Ø§Ù†Ø¬Ø§Ù… Ù†Ø´Ø¯.";
      setError(getFriendlyChatError(errorMessage));
      return;
    }

    setSessions((currentSessions) => {
      const nextSessions = currentSessions.filter((session) => session.id !== sessionId);
      if (activeSessionId === sessionId) {
        const nextActiveSession = nextSessions[0] ?? createSession();
        setActiveSessionId(nextActiveSession.id);
        return nextSessions.length > 0 ? nextSessions : [nextActiveSession];
      }
      return nextSessions.length > 0 ? nextSessions : [createSession()];
    });
    setPendingDeleteSessionId(null);
    setIsMobileHistoryOpen(false);
    setError(null);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sendingRef.current) return;
    const token = getAccessToken();
    const trimmedMessage = message.trim();

    if (!activeSession) {
      return;
    }
    if (!token) {
      setError("برای گفتگو ابتدا وارد پنل شوید.");
      return;
    }
    if (!trimmedMessage) {
      setError("متن سؤال را وارد کنید.");
      return;
    }
    if (selectedCategoryIds.length === 0) {
      const userMsgId = `user-${Date.now()}`;
      const assistantMsgId = `assistant-${Date.now()}`;
      const nextTitle = activeSession.messages.length === 0 ? trimmedMessage.slice(0, 42) : activeSession.title;
      updateSessionById(activeSession.id, (session) => ({
        ...session,
        title: nextTitle || "چت جدید",
        messages: [
          ...session.messages,
          { id: userMsgId, role: "user", content: trimmedMessage },
          { id: assistantMsgId, role: "assistant", content: "لطفاً ابتدا مخزن مورد نظر خود را انتخاب کرده و سپس مجدداً سوال خود را بپرسید." },
        ],
        updatedAt: new Date().toISOString(),
      }));
      setMessage("");
      setError(null);
      return;
    }

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: trimmedMessage,
    };

    const targetSessionId = activeSession.id;
    const targetConversationId = activeSession.conversationId;
    sendingRef.current = true;
    setWaitingSessionId(targetSessionId);
    const nextTitle = activeSession.messages.length === 0 ? trimmedMessage.slice(0, 42) : activeSession.title;
    updateSessionById(targetSessionId, (session) => ({
      ...session,
      title: nextTitle || "چت جدید",
      messages: [...session.messages, userMessage],
      updatedAt: new Date().toISOString(),
    }));
    setMessage("");
    setError(null);
    setIsSending(true);

    try {
      const response = await apiRequest<ChatResponse>("/chat/messages", {
        method: "POST",
        token,
        body: JSON.stringify({
          message: trimmedMessage,
          session_id: targetSessionId,
          conversation_id: targetConversationId,
          category_ids: selectedCategoryIds,
          inline_citations: true,
        }),
      });

      const assistantMessageId = response.message_id ?? `assistant-${Date.now()}`;

      updateSessionById(targetSessionId, (session) => ({
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
  }

  function handleTextareaKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    const isDesktopFinePointer =
      typeof window !== "undefined" && window.matchMedia("(hover: hover) and (pointer: fine)").matches;

    if (!isDesktopFinePointer || event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) {
      return;
    }

    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  const hasMessages = Boolean(activeSession?.messages.length);

  return (
    <section className="chat-layout">
      <button
        aria-expanded={isMobileHistoryOpen}
        aria-label="نمایش چت‌های اخیر"
        className="mobile-chat-history-button"
        onClick={() => setIsMobileHistoryOpen((current) => !current)}
        type="button"
      >
        <span aria-hidden="true" />
        <span aria-hidden="true" />
        <span aria-hidden="true" />
      </button>

      <section className="chat">
        <div className={`chat-panel ${hasMessages ? "has-messages" : "empty"}`}>
          {hasMessages ? (
            <div className="chat-window">
              {activeSession?.messages.map((item) => {
                const isTyping = typingMessageId === item.id;
                const displayContent = getChatDisplayContent(item.content, isTyping, { parseSourceMarkers: item.role === "assistant" });
                if (item.role === "assistant" && isTyping && !displayContent) return null;
                return (
                <article className={`message ${item.role} ${typingMessageId === item.id ? "typing" : ""}`} key={item.id}>
                  {item.role === "assistant"
                    ? <AssistantResponse content={item.content} sources={item.sources} isTyping={isTyping} />
                    : <p>{displayContent}</p>}
                  <button
                    aria-label="کپی متن پیام"
                    className="message-copy-button"
                    disabled={!displayContent}
                    onClick={() => handleCopyMessage(displayContent, item.id)}
                    type="button"
                  >
                    <span aria-hidden="true">⧉</span>
                    {copiedMessageId === item.id ? "کپی شد" : "کپی"}
                  </button>
                </article>
              )})}
              {isWaitingForAnswer ? <ChatPreparingStatus /> : null}
            </div>
          ) : (
            <div className="chat-welcome">
              <div className="chat-orb" aria-hidden="true">
                <Lightbulb size={40} className="glowing-lightbulb" />
              </div>
              <h2>آماده شروع</h2>
              <p>از من بپرسید، ایده بگیرید، تحلیل کنید و سریع‌تر تصمیم بگیرید.</p>
              <div className="gold-divider" aria-hidden="true" />
            </div>
          )}

          <form className="chat-form" onSubmit={handleSubmit}>
            <textarea
              className="textarea chat-input"
              disabled={isSending}
              onChange={(event) => setMessage(event.target.value)}
              onKeyDown={handleTextareaKeyDown}
              placeholder="پیام خود را بنویسید..."
              value={message}
            />
            <div className="chat-form-footer">
              <button
                className="button secondary select-repo-button"
                onClick={() => setIsCategoryModalOpen(true)}
                type="button"
                style={{ minWidth: "180px", gap: "8px" }}
              >
                <span>انتخاب مخزن ({selectedCategoryIds.length})</span>
              </button>
              <button
                className="button send-button"
                disabled={isSending || !message.trim() || selectedCategoryIds.length === 0}
                type="submit"
              >
                <span>ارسال</span>
                <span aria-hidden="true">⌁</span>
              </button>
            </div>
            {error ? <p className="form-error">{error}</p> : null}
          </form>
        </div>
      </section>

      <aside className={`chat-sidebar ${isMobileHistoryOpen ? "open" : ""}`} aria-label="مکالمه‌ها">
        <button className="new-chat-button" onClick={handleNewChat} type="button">
          <span>+</span>
          چت جدید
        </button>
        <div className="recent-heading">
          <span>چت‌های اخیر</span>
          <span aria-hidden="true">◷</span>
        </div>
        <div className="chat-list">
          {visibleSessions.map((session) => (
            <div className={`chat-list-item ${session.id === activeSession?.id ? "active" : ""}`} key={session.id}>
              <button
                className="chat-select-button"
                onClick={() => {
                  setActiveSessionId(session.id);
                  setIsMobileHistoryOpen(false);
                  setError(null);
                }}
                type="button"
              >
                <span className="chat-list-title">{session.title}</span>
                <small>{new Date(session.updatedAt).toLocaleDateString("fa-IR")}</small>
              </button>
              <button
                aria-label="حذف چت"
                className="chat-delete-button"
                onClick={() => setPendingDeleteSessionId(session.id)}
                type="button"
              >
                <Trash2 aria-hidden="true" size={17} strokeWidth={2} />
              </button>
            </div>
          ))}
        </div>
      </aside>

      {isMobileHistoryOpen ? (
        <button
          aria-label="بستن چت‌های اخیر"
          className="mobile-chat-history-backdrop"
          onClick={() => setIsMobileHistoryOpen(false)}
          type="button"
        />
      ) : null}

      {pendingDeleteSession ? (
        <div className="chat-confirm-backdrop" role="presentation" onClick={() => setPendingDeleteSessionId(null)}>
          <div
            className="chat-confirm"
            role="dialog"
            aria-modal="true"
            aria-label="تأیید حذف چت"
            onClick={(event) => event.stopPropagation()}
          >
            <p>از حذف این چت مطمئن هستی؟</p>
            <strong>{pendingDeleteSession.title}</strong>
            <div className="chat-confirm-actions">
              <button className="button danger" onClick={() => handleDeleteSession(pendingDeleteSession.id)} type="button">
                بله
              </button>
              <button className="button secondary" onClick={() => setPendingDeleteSessionId(null)} type="button">
                خیر
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {isCategoryModalOpen ? (
        <div className="chat-confirm-backdrop" role="presentation" onClick={() => setIsCategoryModalOpen(false)}>
          <div
            className="chat-confirm select-repo-modal"
            role="dialog"
            aria-modal="true"
            aria-label="انتخاب مخازن دانش"
            onClick={(event) => event.stopPropagation()}
            style={{ minWidth: "360px" }}
          >
            <p style={{ fontWeight: "bold", marginBottom: "12px", color: "var(--primary)", fontSize: "16px" }}>
              انتخاب مخازن دانش (دسته)
            </p>
            {categories.length === 0 ? (
              <p className="muted" style={{ fontSize: "14px", margin: "10px 0" }}>
                هیچ دسته‌ای یافت نشد. ابتدا در صفحه آپلود فایل دسته بسازید.
              </p>
            ) : (
              <div className="repo-list" style={{ maxHeight: "240px", overflowY: "auto", display: "grid", gap: "10px", margin: "10px 0", textAlign: "right" }}>
                {categories.map((cat) => (
                  <label key={cat.id} className="checkbox-row" style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: "10px", direction: "rtl" }}>
                    <input
                      type="checkbox"
                      checked={selectedCategoryIds.includes(cat.id)}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedCategoryIds((prev) => [...prev, cat.id]);
                        } else {
                          setSelectedCategoryIds((prev) => prev.filter((id) => id !== cat.id));
                        }
                      }}
                      style={{ cursor: "pointer" }}
                    />
                    <span>{cat.name}</span>
                  </label>
                ))}
              </div>
            )}
            <div className="chat-confirm-actions" style={{ marginTop: "14px" }}>
              <button
                className="button"
                onClick={() => {
                  setIsCategoryModalOpen(false);
                  if (activeSessionId) {
                    updateSessionById(activeSessionId, (session) => ({
                      ...session,
                      categoryIds: selectedCategoryIds,
                    }));
                  }
                }}
                type="button"
                style={{ width: "100%" }}
              >
                تایید
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
