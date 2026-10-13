export function ChatPreparingStatus() {
  return (
    <div
      className="message assistant chat-preparing-message"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <p>در حال آماده شدن و ارسال</p>
      <span className="chat-preparing-dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
    </div>
  );
}
