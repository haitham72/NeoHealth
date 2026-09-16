interface Props {
  onOpenTips: () => void;
  /** Toggles the sidebar. Below 768px this opens the off-canvas drawer; at >=768px it
   * collapses/expands the static column instead -- one control, behavior picked by
   * viewport width at click time (see AppShell). Visible at every width now, not just
   * mobile, since desktop needs a way back in once collapsed. */
  onToggleSidebar: () => void;
}

export default function ChatHeader({ onOpenTips, onToggleSidebar }: Props) {
  return (
    <div
      className="flex items-center justify-between gap-3 px-4 py-3 sm:px-6"
      style={{ borderBottom: "1px solid var(--rule)", background: "var(--fhir-surface)", fontFamily: "var(--font-display)" }}
    >
      <div className="flex min-w-0 items-center gap-3">
        <button
          type="button"
          onClick={onToggleSidebar}
          aria-label="Toggle navigation"
          className="-ml-1 grid h-9 w-9 shrink-0 place-items-center rounded-md"
          style={{ border: "1px solid var(--rule)", color: "var(--ink-dim)" }}
        >
          <svg viewBox="0 0 16 16" className="h-4 w-4" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
            <line x1="2" y1="4" x2="14" y2="4" />
            <line x1="2" y1="8" x2="14" y2="8" />
            <line x1="2" y1="12" x2="14" y2="12" />
          </svg>
        </button>
        <div className="min-w-0">
          <h1 className="truncate text-[15px] font-bold" style={{ color: "var(--fhir-dark)" }}>ReguLense</h1>
          <p className="truncate text-[11px]" style={{ color: "var(--ink-faint)" }}>Healthcare Regulation Q&amp;A</p>
        </div>
      </div>
      <button
        type="button"
        onClick={onOpenTips}
        aria-label="Feature tips"
        title="Feature tips"
        className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-[12px] font-bold"
        style={{ border: "1px solid var(--rule)", color: "var(--ink-faint)" }}
      >
        ?
      </button>
    </div>
  );
}
