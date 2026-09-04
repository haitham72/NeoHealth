import type { ReactNode } from "react";
import Sidebar from "./Sidebar";
import type { ChatSummary } from "../types/api";

interface Props {
  children: ReactNode;
  onNewChat: () => void;
  onHome: () => void;
  chats: ChatSummary[];
  activeChatId: string | null;
  onSelectChat: (chatId: string) => void;
}

export default function AppShell({ children, onNewChat, onHome, chats, activeChatId, onSelectChat }: Props) {
  return (
    <div className="flex h-full" style={{ background: "var(--fhir-bg)" }}>
      <Sidebar onNewChat={onNewChat} onHome={onHome} chats={chats} activeChatId={activeChatId} onSelectChat={onSelectChat} />
      <div className="flex flex-1 flex-col min-w-0">{children}</div>
    </div>
  );
}
