import type { SiblingVersion } from "../types/api";

interface Props {
  versions: SiblingVersion[];
  /* Whether "exclude outdated regulations" was actually on for the query that produced
     this citation -- rust/strikethrough must only mark a version as excluded when the
     filter genuinely made it ineligible this time, never as a static per-document flag. */
  filterWasOn: boolean;
}

/* The signature element: the product thesis in one glance — every version that
   exists for this regulation, which one is in force, which are excluded, and which
   one this specific answer is actually grounded in. */
export default function VersionLedger({ versions, filterWasOn }: Props) {
  if (versions.length < 2) return null;

  const sorted = [...versions].sort((a, b) => {
    if (a.is_current !== b.is_current) return a.is_current ? -1 : 1;
    return a.effective_date < b.effective_date ? 1 : -1;
  });

  return (
    <div className="mt-4 flex flex-wrap items-center gap-2">
      <span
        className="text-[11px] font-semibold tracking-[0.1em] uppercase mr-1"
        style={{ color: "var(--ink-faint)", fontFamily: "var(--font-display)" }}
      >
        Version ledger
      </span>
      {sorted.map((v) => {
        // A version only renders as excluded if the filter genuinely excluded it from
        // *this* query's retrieval. With the filter off nothing was excluded, so every
        // chip stays in-force styling -- the one actually cited (is_current) is the
        // chip that gets the checkmark and is sorted first, live per query.
        const excludedThisQuery = filterWasOn && v.superseded;
        return (
          <span
            key={v.id}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-sm text-[12px] font-medium"
            style={{
              fontFamily: "var(--font-display)",
              color: excludedThisQuery ? "var(--superseded-rust)" : "var(--verified-brass)",
              background: excludedThisQuery ? "var(--superseded-rust-bg)" : "var(--verified-brass-bg)",
              textDecoration: excludedThisQuery ? "line-through" : "none",
              // Border reinforces is_current alongside the checkmark -- the sort above
              // already puts this chip first, so together they're unambiguous.
              border: v.is_current ? "1.5px solid currentColor" : "1.5px solid transparent",
            }}
          >
            {v.is_current ? "✓ " : ""}v{v.version}
            <span style={{ opacity: 0.7 }}>&middot; {v.effective_date}</span>
          </span>
        );
      })}
    </div>
  );
}
