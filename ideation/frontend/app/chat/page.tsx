import { ChatPanel } from "@/features/chat/components/chat-panel";
import { AppShell } from "@/components/layout/app-shell";

export default function ChatPage() {
  return (
    <AppShell>
      <h1 className="page-title">گفتگو با دانش داخلی</h1>
      <p className="page-subtitle">
        پاسخ‌ها از مسیر backend به Chatflow در Dify ارسال می‌شوند و باید فقط بر اساس اسناد داخلی تولید شوند.
      </p>
      <ChatPanel />
    </AppShell>
  );
}
