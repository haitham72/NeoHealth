import { useCorpusStats } from "../api/client";
import type { ChatSummary } from "../types/api";

interface Props {
  onNewChat: () => void;
  onHome: () => void;
  chats: ChatSummary[];
  activeChatId: string | null;
  onSelectChat: (chatId: string) => void;
  /** Drawer state -- only consulted below the md breakpoint; at >=768px the sidebar is
   * a static column and the transform is reset unconditionally by `md:translate-x-0`. */
  open: boolean;
  onClose: () => void;
  /** Desktop-only collapse (>=768px); ignored below md, where `open`/`onClose` govern
   * the off-canvas drawer instead. Collapsing sets width to 0 rather than unmounting,
   * so the corpus-stats fetch and chat list scroll position survive a toggle. */
  collapsed: boolean;
}

export default function Sidebar({ onNewChat, onHome, chats, activeChatId, onSelectChat, open, onClose, collapsed }: Props) {
  const { data: stats } = useCorpusStats();

  return (
    <aside
      // Mobile: fixed off-canvas drawer sliding in from the left above the scrim.
      // Desktop (md+): a collapsible flex column -- z-index is scoped to mobile only
      // (md:z-auto) because a flex/grid item's z-index applies even at `position:
      // static` (a CSS special case), so the un-scoped z-50 this used to carry sat
      // above every full-page overlay's z-40 (onboarding, home) despite `position:
      // static` making it "just a column" -- the fix that actually mattered.
      className={`fixed inset-y-0 left-0 z-50 flex w-[80vw] max-w-[280px] shrink-0 flex-col gap-4 overflow-hidden p-4 transition-all duration-200 ease-out md:static md:z-auto md:max-w-none md:translate-x-0 ${
        open ? "translate-x-0" : "-translate-x-full"
      } ${collapsed ? "md:w-0 md:min-w-0 md:p-0 md:opacity-0" : "md:w-[240px] md:opacity-100"}`}
      style={{ background: "var(--fhir-dark)", color: "#fff", fontFamily: "var(--font-display)" }}
      aria-hidden={collapsed || undefined}
    >
      {/* Explicit close affordance -- the scrim works, but a visible control is what
          users reach for first on a phone. Absolutely positioned so the desktop
          layout below it stays exactly as it was. */}
      <button
        type="button"
        onClick={onClose}
        aria-label="Close navigation"
        className="absolute right-3 top-3 z-10 grid h-9 w-9 place-items-center rounded-md text-[18px] leading-none md:hidden"
        style={{ background: "rgba(255,255,255,0.12)", color: "#fff" }}
      >
        &times;
      </button>

      <button
        type="button"
        onClick={onHome}
        aria-label="ReguLense home -- replay the introduction"
        className="flex flex-col items-start gap-4 rounded-md border-0 bg-transparent p-0 text-left cursor-pointer"
      >
        <div className="rounded-md p-2.5" style={{ background: "#fff" }}>
          <svg viewBox="0 0 40 40" className="w-full h-auto" role="img" aria-label="ReguLense">
            <circle cx="17" cy="17" r="12" fill="none" stroke="var(--fhir-blue)" strokeWidth="3" />
            <line x1="26" y1="26" x2="35" y2="35" stroke="var(--fhir-dark)" strokeWidth="4" strokeLinecap="round" />
            <path d="M11 17l4 4 8-8" fill="none" stroke="var(--fhir-dark)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>

        <div className="flex items-center gap-2 px-1">
          <span aria-hidden style={{ fontSize: 18 }}>+</span>
          <span className="text-[15px] font-bold tracking-[0.06em]">ReguLense</span>
        </div>
      </button>

      <button
        type="button"
        onClick={onNewChat}
        className="flex items-center justify-center gap-2 rounded-md px-3 py-2.5 text-[13px] font-semibold"
        style={{ background: "var(--fhir-blue)", color: "#fff" }}
      >
        New Chat
      </button>

      {chats.length > 0 && (
        <div className="flex flex-1 min-h-0 flex-col gap-1 overflow-y-auto">
          <span
            className="px-2 pb-1 text-[10px] font-bold uppercase tracking-wide"
            style={{ color: "rgba(255,255,255,0.45)" }}
          >
            Recent
          </span>
          {chats.map((chat) => (
            <button
              key={chat.id}
              type="button"
              onClick={() => onSelectChat(chat.id)}
              title={chat.title ?? "New conversation"}
              className="truncate rounded-md px-3 py-2 text-left text-[12px]"
              style={{
                background: chat.id === activeChatId ? "rgba(255,255,255,0.14)" : "transparent",
                color: chat.id === activeChatId ? "#fff" : "rgba(255,255,255,0.7)",
              }}
            >
              {chat.title ?? "New conversation"}
            </button>
          ))}
        </div>
      )}

      {stats && (
        <div className="mt-auto rounded-md px-3 py-2 text-[11px] leading-relaxed" style={{ background: "rgba(255,255,255,0.06)" }}>
          {stats.official_documents} regulations, {stats.official_chunks} chunks
          {stats.research_documents > 0 && ` + ${stats.research_documents} research papers`}
        </div>
      )}
    </aside>
  );
}
