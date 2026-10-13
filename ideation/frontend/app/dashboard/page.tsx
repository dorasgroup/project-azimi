import { ChatPanel } from "@/components/chat/chat-panel";
import { AppShell } from "@/components/layout/app-shell";

export default function DashboardPage() {
  return (
    <AppShell>
      <div className="dashboard-chat">
        <ChatPanel />
      </div>
    </AppShell>
  );
}
