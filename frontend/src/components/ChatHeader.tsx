interface Props {
  onOpenTips: () => void;
}

export default function ChatHeader({ onOpenTips }: Props) {
  return (
    <div
      className="flex items-center justify-between px-6 py-3"
      style={{ borderBottom: "1px solid var(--rule)", background: "var(--fhir-surface)", fontFamily: "var(--font-display)" }}
    >
      <div>
        <h1 className="text-[15px] font-bold" style={{ color: "var(--fhir-dark)" }}>ReguLense</h1>
        <p className="text-[11px]" style={{ color: "var(--ink-faint)" }}>Healthcare Regulation Q&amp;A</p>
      </div>
      <button
        type="button"
        onClick={onOpenTips}
        aria-label="Feature tips"
        title="Feature tips"
        className="grid h-6 w-6 place-items-center rounded-full text-[12px] font-bold"
        style={{ border: "1px solid var(--rule)", color: "var(--ink-faint)" }}
      >
        ?
      </button>
    </div>
  );
}
