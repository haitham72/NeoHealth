import { useCorpusStats } from "../api/client";

interface Props {
  onNewChat: () => void;
}

export default function Sidebar({ onNewChat }: Props) {
  const { data: stats } = useCorpusStats();

  return (
    <aside
      className="flex h-full w-[240px] shrink-0 flex-col gap-4 p-4"
      style={{ background: "var(--fhir-dark)", color: "#fff", fontFamily: "var(--font-display)" }}
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

      <button
        type="button"
        onClick={onNewChat}
        className="flex items-center gap-2 rounded-md px-3 py-2 text-[13px] font-semibold"
        style={{ background: "var(--fhir-blue)", color: "#fff" }}
      >
        New Chat
      </button>

      {stats && (
        <div className="mt-2 rounded-md px-3 py-2 text-[11px] leading-relaxed" style={{ background: "rgba(255,255,255,0.06)" }}>
          {stats.official_documents} regulations, {stats.official_chunks} chunks
          {stats.research_documents > 0 && ` + ${stats.research_documents} research papers`}
        </div>
      )}
    </aside>
  );
}
