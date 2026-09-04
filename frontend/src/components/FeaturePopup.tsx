import { useEffect, useState, type KeyboardEvent } from "react";

export const FEATURE_POPUP_STORAGE_KEY = "regulense-feature-popup-v1";
const SEEN = "seen";

export function hasSeenFeaturePopup(): boolean {
  try {
    return localStorage.getItem(FEATURE_POPUP_STORAGE_KEY) === SEEN;
  } catch {
    return false;
  }
}

export function markFeaturePopupSeen(): void {
  try {
    localStorage.setItem(FEATURE_POPUP_STORAGE_KEY, SEEN);
  } catch {
    // The popup still works this visit without persistence; it'll just resurface next time.
  }
}

const PANELS = [
  {
    eyebrow: "Superseded versions",
    title: "Outdated regulations are excluded by default.",
    body: "The “Exclude outdated regulations” checkbox in the composer starts checked. Uncheck it to include superseded versions in retrieval — every version still shows in the ledger below an answer, with the one in force marked.",
    preview: (
      <div className="rounded-md p-4 text-[12px]" style={{ background: "var(--fhir-bg)", border: "1px solid var(--rule)" }}>
        <div className="flex items-center justify-between">
          <span className="font-semibold" style={{ color: "var(--fhir-dark)" }}>Standards for Clinics</span>
          <span className="rounded px-2 py-0.5 font-semibold" style={{ background: "var(--verified-brass-bg)", color: "var(--verified-brass)" }}>v3 &middot; in force</span>
        </div>
        <div className="mt-2.5 flex items-center justify-between" style={{ color: "var(--ink-faint)" }}>
          <span>v2 &middot; 2022</span>
          <span style={{ color: "var(--superseded-rust)" }}>superseded</span>
        </div>
        <p className="mt-2.5" style={{ color: "var(--superseded-rust)" }}>1 superseded version of this document was excluded from retrieval.</p>
      </div>
    ),
  },
  {
    eyebrow: "Version comparison",
    title: "Compare a citation against the version it replaced.",
    body: "Any cited clause can be checked against its previous version with one click — a plain-language explanation of what changed, not just a document diff.",
    preview: (
      <div className="flex justify-end rounded-md p-4" style={{ background: "var(--fhir-bg)", border: "1px solid var(--rule)" }}>
        <span
          className="inline-flex items-center gap-2 rounded-md px-4 py-2.5 text-[12px] font-semibold uppercase tracking-[0.04em]"
          style={{ background: "var(--verified-brass)", color: "#fff" }}
        >
          <span aria-hidden>&#8644;</span> See what changed
        </span>
      </div>
    ),
  },
  {
    eyebrow: "Source trail",
    title: "Every citation opens the exact passage in its PDF.",
    body: "Click a citation to see its document code, version, and page — then “View in PDF” jumps straight to the highlighted passage, not a guessed location.",
    preview: (
      <div className="rounded-md p-4 text-[12px]" style={{ background: "var(--fhir-bg)", border: "1px solid var(--rule)" }}>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5" style={{ color: "var(--ink-dim)" }}>
          <span style={{ color: "var(--ink-faint)" }}>Code</span><span>DHA-STD-2024-07</span>
          <span style={{ color: "var(--ink-faint)" }}>Page</span><span>42</span>
        </div>
        <span className="mt-2.5 inline-block font-semibold" style={{ color: "var(--fhir-blue)" }}>View in PDF &#8599;</span>
      </div>
    ),
  },
] as const;

interface Props {
  onClose: () => void;
}

/** A deliberately heavier, blocking walkthrough -- dimmed backdrop, a horizontal
 * sliding deck between panels -- distinct on purpose from the main onboarding's
 * vertical scroll: this is a short modal interruption shown once, not a page. */
export default function FeaturePopup({ onClose }: Props) {
  const [index, setIndex] = useState(0);
  const isLast = index === PANELS.length - 1;

  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  const next = () => (isLast ? onClose() : setIndex((i) => i + 1));
  const back = () => setIndex((i) => Math.max(0, i - 1));

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      next();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      back();
    }
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4" role="dialog" aria-modal="true" aria-label="ReguLense feature tips">
      <button
        type="button"
        aria-label="Close"
        className="absolute inset-0 cursor-default"
        style={{ background: "rgba(2,48,71,0.55)" }}
        onClick={onClose}
      />
      <div
        className="relative w-full max-w-[640px] overflow-hidden rounded-2xl"
        style={{ background: "var(--fhir-surface)", boxShadow: "0 32px 80px rgba(2,48,71,0.35)", fontFamily: "var(--font-system)" }}
        onKeyDown={handleKeyDown}
        tabIndex={-1}
        // eslint-disable-next-line jsx-a11y/no-autofocus -- a modal that blocks the page should take focus immediately
        ref={(el) => el?.focus()}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Dismiss"
          className="absolute right-4 top-4 z-10 grid h-7 w-7 place-items-center rounded-full text-[15px]"
          style={{ background: "var(--fhir-bg)", color: "var(--ink-faint)" }}
        >
          &times;
        </button>

        <div className="overflow-hidden">
          <div
            className="flex"
            style={{ transform: `translateX(-${index * 100}%)`, transition: "transform 420ms cubic-bezier(0.22,1,0.36,1)" }}
          >
            {PANELS.map((panel) => (
              <div key={panel.eyebrow} className="w-full shrink-0 px-9 pb-8 pt-12">
                <span className="text-[11px] font-bold uppercase tracking-[0.1em]" style={{ color: "var(--fhir-blue)", fontFamily: "var(--font-system-mono)" }}>{panel.eyebrow}</span>
                <h3 className="mt-3 text-[22px] font-semibold leading-snug tracking-[-0.02em]" style={{ color: "var(--fhir-dark)" }}>{panel.title}</h3>
                <p className="mt-3 text-[14px] leading-relaxed" style={{ color: "var(--ink-dim)" }}>{panel.body}</p>
                <div className="mt-5">{panel.preview}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between border-t px-9 py-5" style={{ borderColor: "var(--rule)" }}>
          <div className="flex gap-2" aria-hidden>
            {PANELS.map((p, i) => (
              <span
                key={p.eyebrow}
                className="h-1.5 rounded-full"
                style={{ width: i === index ? 22 : 6, background: i === index ? "var(--fhir-blue)" : "var(--rule)", transition: "width 250ms ease, background 250ms ease" }}
              />
            ))}
          </div>
          <div className="flex items-center gap-4">
            {index > 0 && (
              <button type="button" onClick={back} className="text-[12px] font-semibold" style={{ color: "var(--ink-faint)" }}>
                Back
              </button>
            )}
            <button
              type="button"
              onClick={next}
              className="rounded-[10px] px-5 py-2.5 text-[13px] font-semibold"
              style={{ background: "var(--fhir-blue)", color: "#fff", boxShadow: "0 8px 20px rgba(0,119,182,0.28)" }}
            >
              {isLast ? "Got it" : "Next"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
