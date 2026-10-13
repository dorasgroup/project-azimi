"use client";

import { Trash2, Lightbulb } from "lucide-react";
import { FormEvent, KeyboardEvent, useState, useMemo } from "react";
import { useChatSessionStore } from "../hooks/use-chat-session";
import { useChatStream } from "../hooks/use-chat-stream";
import { useCategories } from "../../categories/hooks/use-categories";
import { getChatDisplayContent } from "@/lib/chat/display-content";
import { ChatPreparingStatus } from "@/components/chat/chat-preparing-status";
import { AssistantResponse } from "@/components/chat/assistant-response";

export function ChatPanel() {
  const [message, setMessage] = useState("");
  const [isMobileHistoryOpen, setIsMobileHistoryOpen] = useState(false);
  const [pendingDeleteSessionId, setPendingDeleteSessionId] = useState<string | null>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [isCategoryModalOpen, setIsCategoryModalOpen] = useState(false);
  const [selectedCategoryIds, setSelectedCategoryIds] = useState<string[]>([]);

  // Hooks
  const { data: categories = [] } = useCategories();
  const { sessions, activeSessionId, setActiveSessionId, addSession, deleteSession, updateSession } = useChatSessionStore();
  const { isSending, waitingSessionId, typingMessageId, error, setError, sendMessage } = useChatStream();

  const activeSession = sessions.find(s => s.id === activeSessionId) ?? sessions[0];
  const pendingDeleteSession = sessions.find(s => s.id === pendingDeleteSessionId);
  const visibleSessions = sessions; // Store already filters out empty ones

  // Initialization check for category selection based on active session
  useMemo(() => {
    if (activeSession) {
      setSelectedCategoryIds(activeSession.categoryIds || []);
    }
  }, [activeSession?.id]);

  const isWaitingForAnswer = waitingSessionId !== null && waitingSessionId === activeSession?.id;

  async function handleCopyMessage(content: string, messageId: string) {
    if (!content) return;
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw new Error("Clipboard API unavailable");
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
    setTimeout(() => setCopiedMessageId((id) => (id === messageId ? null : id)), 1400);
  }

  function handleNewChat() {
    if (activeSession && activeSession.messages.length === 0) {
      setIsMobileHistoryOpen(false);
      setMessage("");
      setError(null);
      return;
    }
    addSession();
    setIsMobileHistoryOpen(false);
    setMessage("");
    setError(null);
  }

  function handleDeleteSession(sessionId: string) {
    deleteSession(sessionId);
    setPendingDeleteSessionId(null);
    setIsMobileHistoryOpen(false);
    setError(null);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSending) return;
    const trimmedMessage = message.trim();
    if (!trimmedMessage) {
      setError("متن سؤال را وارد کنید.");
      return;
    }
    setMessage("");
    await sendMessage(trimmedMessage, selectedCategoryIds);
  }

  function handleTextareaKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    const isDesktopFinePointer = typeof window !== "undefined" && window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    if (!isDesktopFinePointer || event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
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
        <span aria-hidden="true" /><span aria-hidden="true" /><span aria-hidden="true" />
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
          <span>+</span> چت جدید
        </button>
        <div className="recent-heading">
          <span>چت‌های اخیر</span><span aria-hidden="true">◷</span>
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
                    updateSession(activeSessionId, (session) => ({
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
