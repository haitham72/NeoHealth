import { useEffect, useState, type ReactNode } from "react";
import Sidebar from "./Sidebar";
import type { ChatSummary } from "../types/api";

const SIDEBAR_COLLAPSED_KEY = "regulense-sidebar-collapsed";

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

function saveCollapsed(value: boolean): void {
  try {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, value ? "1" : "0");
  } catch {
    // Falls back to defaulting open next visit -- not worth failing the toggle over.
  }
}

interface Props {
  children: (api: { onToggleSidebar: () => void }) => ReactNode;
  onNewChat: () => void;
  onHome: () => void;
  chats: ChatSummary[];
  activeChatId: string | null;
  onSelectChat: (chatId: string) => void;
}

/** Shell layout. Below 768px the sidebar is an off-canvas drawer over the conversation
 * (the ChatGPT/Claude mobile pattern) -- a 240px column permanently docked on a 390px
 * phone left the chat ~150px wide, which is what made the prompt chips wrap into
 * unreadable slivers. At >=768px it's a static column by default, but collapsible via
 * the same header toggle (remembered in localStorage as a layout preference, not a
 * one-time "have you seen this" flag). One button drives both: which behavior fires is
 * decided by the viewport width *at click time* (matchMedia), since CSS -- not React
 * state -- is what decides which of the two mechanisms (drawer vs. collapse) is even
 * visible to the user at that width. */
export default function AppShell({ children, onNewChat, onHome, chats, activeChatId, onSelectChat }: Props) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(loadCollapsed);
  const close = () => setSidebarOpen(false);

  const toggleSidebar = () => {
    const isDesktop = window.matchMedia("(min-width: 768px)").matches;
    if (isDesktop) {
      setSidebarCollapsed((collapsed) => {
        saveCollapsed(!collapsed);
        return !collapsed;
      });
    } else {
      setSidebarOpen((open) => !open);
    }
  };

  // Escape closes the drawer, matching the app's other overlays (onboarding, popups).
  useEffect(() => {
    if (!sidebarOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSidebarOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sidebarOpen]);

  return (
    <div className="flex h-full" style={{ background: "var(--fhir-bg)" }}>
      {/* Scrim: mobile only, and only while open -- md:hidden keeps it out of the way
          of the static desktop layout even if state is somehow left true on resize. */}
      {sidebarOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={close}
          className="fixed inset-0 z-40 md:hidden"
          style={{ background: "rgba(10, 18, 32, 0.45)", border: 0 }}
        />
      )}

      <Sidebar
        open={sidebarOpen}
        onClose={close}
        collapsed={sidebarCollapsed}
        onNewChat={() => {
          onNewChat();
          close();
        }}
        onHome={() => {
          onHome();
          close();
        }}
        chats={chats}
        activeChatId={activeChatId}
        onSelectChat={(id) => {
          onSelectChat(id);
          close();
        }}
      />

      <div className="flex flex-1 flex-col min-w-0">{children({ onToggleSidebar: toggleSidebar })}</div>
    </div>
  );
}
