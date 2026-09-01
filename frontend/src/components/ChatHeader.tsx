export default function ChatHeader() {
  return (
    <div
      className="flex items-center justify-between px-6 py-3"
      style={{ borderBottom: "1px solid var(--rule)", background: "var(--fhir-surface)", fontFamily: "var(--font-display)" }}
    >
      <div>
        <h1 className="text-[15px] font-bold" style={{ color: "var(--fhir-dark)" }}>ReguLense</h1>
        <p className="text-[11px]" style={{ color: "var(--ink-faint)" }}>Healthcare Regulation Q&amp;A</p>
      </div>
    </div>
  );
}
