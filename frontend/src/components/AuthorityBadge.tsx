import type { DocumentTier } from "../types/api";

interface Props {
  authority: string;
  tier: DocumentTier;
}

// Known government regulators get a stable code + institutional color. An official
// authority this table doesn't know yet (e.g. a newly ingested DHCC/SHA doc) falls
// back to a derived acronym in a neutral color rather than crashing or mislabeling it
// -- add a real entry here once that authority is confirmed.
const OFFICIAL_AUTHORITIES: Record<string, { code: string; color: string; bg: string }> = {
  "dubai health authority": { code: "DHA", color: "var(--dha-steel)", bg: "var(--dha-steel-bg)" },
  "department of health - abu dhabi": { code: "DoH", color: "var(--doh-teal)", bg: "var(--doh-teal-bg)" },
  "ministry of health and prevention": { code: "MOHAP", color: "var(--mohap-plum)", bg: "var(--mohap-plum-bg)" },
};

function acronym(authority: string): string {
  const letters = authority
    .split(/\s+/)
    .filter((w) => w.length > 2)
    .map((w) => w[0]);
  return (letters.length >= 2 ? letters : [authority[0] ?? "?"]).join("").slice(0, 5).toUpperCase();
}

/** Shared with CrossCheckRegulation's document chips, so a DHA/DoH reference always
 * gets the same code + color wherever it's shown, not a second, drifting color table. */
export function getOfficialAuthorityStyle(authority: string): { code: string; color: string; bg: string } {
  const known = OFFICIAL_AUTHORITIES[authority.toLowerCase()];
  return {
    code: known?.code ?? acronym(authority),
    color: known?.color ?? "var(--ink-dim)",
    bg: known?.bg ?? "var(--surface)",
  };
}

/* The "logo": not a scraped government seal, a document-seal-styled chip instead --
   a double-rule border evokes a certificate frame without borrowing real insignia.
   Reserved for tier === "official" -- a research paper or a law firm's commentary
   never gets dressed up as if it carried a regulator's authority. */
export default function AuthorityBadge({ authority, tier }: Props) {
  if (tier !== "official") {
    const label = tier === "research" ? "RESEARCH" : "COMMENTARY";
    return (
      <span
        className="group relative inline-flex items-center px-2.5 py-1 text-[11px] font-semibold tracking-[0.12em] rounded-sm cursor-default"
        style={{
          color: "var(--ink-faint)",
          background: "var(--paper)",
          border: "1px dashed var(--rule)",
          fontFamily: "var(--font-display)",
        }}
      >
        {label}
        <span
          role="tooltip"
          className="pointer-events-none absolute left-1/2 top-full z-10 mt-2 -translate-x-1/2 whitespace-nowrap rounded-sm px-2 py-1 text-[11px] font-medium opacity-0 shadow-md transition-opacity group-hover:opacity-100"
          style={{ background: "var(--ink)", color: "var(--surface)" }}
        >
          {authority}
        </span>
      </span>
    );
  }

  const { code, color, bg } = getOfficialAuthorityStyle(authority);

  return (
    <span
      className="group relative inline-flex items-center px-2.5 py-1 text-[11px] font-semibold tracking-[0.12em] rounded-sm cursor-default"
      style={{
        color,
        background: bg,
        border: `1px solid ${color}`,
        boxShadow: `0 0 0 2px ${bg}, 0 0 0 3px ${color}`,
        fontFamily: "var(--font-display)",
      }}
    >
      {code}
      <span
        role="tooltip"
        className="pointer-events-none absolute left-1/2 top-full z-10 mt-2 -translate-x-1/2 whitespace-nowrap rounded-sm px-2 py-1 text-[11px] font-medium opacity-0 shadow-md transition-opacity group-hover:opacity-100"
        style={{ background: "var(--ink)", color: "var(--surface)" }}
      >
        {authority}
      </span>
    </span>
  );
}
