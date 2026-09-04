import { useEffect, useRef, useState, type KeyboardEvent } from "react";

export const ONBOARDING_STORAGE_KEY = "regulense-onboarding-v2";
const COMPLETE = "complete";

function markOnboardingComplete(): void {
  try {
    sessionStorage.setItem(ONBOARDING_STORAGE_KEY, COMPLETE);
  } catch {
    // The current visit can still continue without session storage.
  }
}

function StampField() {
  return (
    <div className="onboarding-stamp-field">
      <div className="onboarding-stamp-doc-line" />
      <div className="onboarding-stamp-doc-line" />
      <div className="onboarding-stamp-doc-line" />
      <div className="onboarding-ink-stamp onboarding-ink-stamp-dha"><div><span>DHA</span><small>DUBAI</small></div></div>
      <div className="onboarding-ink-stamp onboarding-ink-stamp-doh"><div><span>DoH</span><small>ABU DHABI</small></div></div>
      <div className="onboarding-ink-stamp onboarding-ink-stamp-mohap"><div><span>MOHAP</span><small>FEDERAL</small></div></div>
    </div>
  );
}

function GaugeCard({ active }: { active: boolean }) {
  const [count, setCount] = useState(0);
  const played = useRef(false);

  useEffect(() => {
    if (!active) {
      played.current = false;
      setCount(0);
      return;
    }
    if (played.current) return;
    played.current = true;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setCount(0.87);
      return;
    }
    const start = performance.now();
    const duration = 1000;
    let raf = 0;
    const frame = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setCount(eased * 0.87);
      if (t < 1) raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [active]);

  return (
    <div className="onboarding-gauge-card">
      <div className="onboarding-gauge-label">Evidence Strength &mdash; Calibrated</div>
      <svg viewBox="0 0 220 120" width="100%" height="100" aria-hidden="true">
        <path d="M20,110 A90,90 0 0,1 200,110" fill="none" stroke="var(--case-rule)" strokeWidth="14" strokeLinecap="round" />
        <path className="onboarding-gauge-arc" d="M20,110 A90,90 0 0,1 200,110" fill="none" stroke="var(--case-brass)" strokeWidth="14" strokeLinecap="round" />
        <g className="onboarding-gauge-needle">
          <line x1="110" y1="110" x2="168" y2="52" stroke="var(--case-ink)" strokeWidth="3" strokeLinecap="round" />
          <circle cx="110" cy="110" r="6" fill="var(--case-ink)" />
        </g>
      </svg>
      <div className="onboarding-gauge-readout">{count.toFixed(2)}<span>HIGH CONFIDENCE</span></div>
      <div className="onboarding-gauge-note">&#9873; Below the evidence floor: no fluent guess, only the abstention.</div>
    </div>
  );
}

function DocLeaf() {
  return (
    <div className="onboarding-doc-leaf">
      <div className="onboarding-doc-leaf-head"><span>Standards for Clinics</span><span>Ex. 4.2 &mdash; p. 42/66</span></div>
      <div className="onboarding-doc-leaf-body">
        <h3>4.2 Licensing Requirements</h3>
        <p>Healthcare professionals must hold a <mark>valid license issued by the authority</mark> before practicing within the emirate.</p>
        <p>Applications are reviewed against the <mark>current qualification requirements</mark> in force at the time of submission.</p>
        <div className="onboarding-cite-tag">[1] Exact passage highlighted, not approximated</div>
      </div>
    </div>
  );
}

const STEPS = [
  {
    marker: "01",
    eyebrow: "Context before conclusions",
    title: "The right rule for the right place.",
    body: "Every clause is stamped with the authority that issued it. ReguLense keeps DHA, DoH, and MOHAP guidance separated, so an answer never quietly borrows from the wrong jurisdiction.",
    render: () => <StampField />,
  },
  {
    marker: "02",
    eyebrow: "Calibrated confidence",
    title: "Confidence you can read on the gauge.",
    body: "Strong evidence gets a direct answer. Weak evidence gets a clear caveat. Below the calibrated floor, ReguLense abstains rather than guess.",
    render: (active: boolean) => <GaugeCard active={active} />,
  },
  {
    marker: "03",
    eyebrow: "Evidence, not decoration",
    title: "Every answer leaves a paper trail.",
    body: "Open the cited passage in its source PDF, with the exact governing line highlighted — not a guessed excerpt, the actual bounding box of the clause.",
    render: () => <DocLeaf />,
  },
] as const;

interface Props {
  onComplete: () => void;
}

/** A compact, blocking modal wizard shown the first time a visitor reaches the chat --
 * deliberately distinct from a full-page takeover: a dimmed backdrop, a two-panel
 * dialog (static brand identity + a stepped, sliding content deck), Skip always
 * available. Session-persisted the same way as before. */
export default function OnboardingWelcome({ onComplete }: Props) {
  const [step, setStep] = useState(0);
  const modalRef = useRef<HTMLDivElement | null>(null);
  const isLast = step === STEPS.length - 1;

  const complete = () => {
    markOnboardingComplete();
    onComplete();
  };
  const next = () => (isLast ? complete() : setStep((s) => s + 1));
  const back = () => setStep((s) => Math.max(0, s - 1));

  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  useEffect(() => {
    modalRef.current?.focus();
  }, []);

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      complete();
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      next();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      back();
    }
  };

  return (
    <div
      className="onboarding-screen"
      role="presentation"
      onClick={complete}
      onKeyDown={handleKeyDown}
    >
      <div
        ref={modalRef}
        className="onboarding-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Welcome to ReguLense"
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="onboarding-brand-panel">
          <div className="onboarding-brand-glow" aria-hidden="true" />
          <div className="onboarding-brand">
            <span className="onboarding-brand-mark">R</span>
            <span>ReguLense</span>
            <span className="onboarding-brand-dot" aria-hidden="true" />
          </div>
          <h1 className="onboarding-brand-headline">The rule,<br />without <em>the noise.</em></h1>
          <p className="onboarding-brand-tagline">
            A research companion for compliance questions across the UAE health system.
          </p>
          <div className="onboarding-brand-seals" aria-hidden="true">
            <span className="onboarding-brand-seal">DHA</span>
            <span className="onboarding-brand-seal">DoH</span>
            <span className="onboarding-brand-seal">MOH</span>
          </div>
        </div>

        <div className="onboarding-content-panel">
          <button type="button" className="onboarding-close" onClick={complete} aria-label="Skip introduction">
            &times;
          </button>
          <div className="onboarding-modal-track-wrap">
            <div className="onboarding-modal-track" style={{ transform: `translateX(-${step * 100}%)` }}>
              {STEPS.map((item, index) => (
                <div key={item.marker} className={`onboarding-modal-step${index === step ? " is-active" : ""}`}>
                  <div className="onboarding-step-head">
                    <span className="onboarding-eyebrow">{item.eyebrow}</span>
                    <span className="onboarding-step-count">{item.marker} / 03</span>
                  </div>
                  <h2>{item.title}</h2>
                  <p className="onboarding-body">{item.body}</p>
                  <div className="onboarding-visual-wrap">{item.render(index === step)}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="onboarding-modal-footer">
            <div className="onboarding-progress" aria-label={`Step ${step + 1} of ${STEPS.length}`}>
              {STEPS.map((item, index) => (
                <span key={item.marker} className={index === step ? "active" : ""} />
              ))}
            </div>
            <div className="onboarding-footer-actions">
              {step > 0 && (
                <button type="button" className="onboarding-secondary" onClick={back}>Back</button>
              )}
              <button type="button" className="onboarding-secondary" onClick={complete}>Skip</button>
              <button type="button" className="onboarding-primary" onClick={next}>
                {isLast ? "Begin Your Case" : "Continue"}<span aria-hidden>&rarr;</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
