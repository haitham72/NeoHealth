import type { ReactNode } from "react";
import Sidebar from "./Sidebar";

interface Props {
  children: ReactNode;
  onNewChat: () => void;
}

export default function AppShell({ children, onNewChat }: Props) {
  return (
    <div className="flex h-full" style={{ background: "var(--fhir-bg)" }}>
      <Sidebar onNewChat={onNewChat} />
      <div className="flex flex-1 flex-col min-w-0">{children}</div>
    </div>
  );
}
