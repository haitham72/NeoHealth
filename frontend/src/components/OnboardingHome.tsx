import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";

export const ONBOARDING_HOME_STORAGE_KEY = "regulense-onboarding-home-v1";
const COMPLETE = "complete";

function markHomeComplete(): void {
  try {
    sessionStorage.setItem(ONBOARDING_HOME_STORAGE_KEY, COMPLETE);
  } catch {
    // The current visit can still continue without session storage.
  }
}

function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function Ticket() {
  return (
    <div className="home-ticket">
      <div className="home-ticket-head">
        <span>Intake Ticket</span>
        <span className="home-stamp-live">Live</span>
      </div>
      <div className="home-ticket-q">&quot;Can a facility renew a rejected license?&quot;</div>
      <div className="home-ticket-meta">
        <div className="home-ticket-row"><span>Status</span><b>Retrieving governing clause</b></div>
        <div className="home-ticket-row"><span>Jurisdiction check</span><span className="home-dot">&#9679;</span></div>
        <div className="home-ticket-row"><span>Version check</span><span className="home-dot">&#9679;</span></div>
        <div className="home-ticket-row"><span>Citation</span><span>pending&hellip;</span></div>
      </div>
    </div>
  );
}

function StampField() {
  return (
    <div className="home-stamp-field">
      <div className="home-stamp-doc-line" />
      <div className="home-stamp-doc-line" />
      <div className="home-stamp-doc-line" />
      <div className="home-ink-stamp home-ink-stamp-dha"><div><span>DHA</span><small>DUBAI</small></div></div>
      <div className="home-ink-stamp home-ink-stamp-doh"><div><span>DoH</span><small>ABU DHABI</small></div></div>
      <div className="home-ink-stamp home-ink-stamp-mohap"><div><span>MOHAP</span><small>FEDERAL</small></div></div>
    </div>
  );
}

function GaugeCard() {
  const [count, setCount] = useState(0);
  const started = useRef(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const section = el.closest(".home-section");
    if (!section) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting || started.current) continue;
          started.current = true;
          if (prefersReducedMotion()) {
            setCount(0.87);
            return;
          }
          const start = performance.now();
          const duration = 1000;
          const frame = (now: number) => {
            const t = Math.min(1, (now - start) / duration);
            const eased = 1 - Math.pow(1 - t, 3);
            setCount(eased * 0.87);
            if (t < 1) requestAnimationFrame(frame);
          };
          requestAnimationFrame(frame);
        }
      },
      { threshold: 0.4 }
    );
    observer.observe(section);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="home-gauge-card" ref={wrapRef}>
      <div className="home-gauge-label">Evidence Strength &mdash; Calibrated</div>
      <svg viewBox="0 0 220 120" width="100%" height="140" aria-hidden="true">
        <path d="M20,110 A90,90 0 0,1 200,110" fill="none" stroke="var(--home-rule)" strokeWidth="14" strokeLinecap="round" />
        <path className="home-gauge-arc" d="M20,110 A90,90 0 0,1 200,110" fill="none" stroke="var(--home-brass)" strokeWidth="14" strokeLinecap="round" />
        <g className="home-gauge-needle">
          <line x1="110" y1="110" x2="168" y2="52" stroke="var(--home-ink)" strokeWidth="3" strokeLinecap="round" />
          <circle cx="110" cy="110" r="6" fill="var(--home-ink)" />
        </g>
      </svg>
      <div className="home-gauge-readout">{count.toFixed(2)}<span>HIGH CONFIDENCE</span></div>
      <div className="home-gauge-note">&#9873; Below the evidence floor: no fluent guess, only the abstention.</div>
    </div>
  );
}

function DocLeaf() {
  return (
    <div className="home-doc-leaf">
      <div className="home-doc-leaf-head"><span>Standards for Clinics</span><span>Ex. 4.2 &mdash; p. 42/66</span></div>
      <div className="home-doc-leaf-body">
        <h3>4.2 Licensing Requirements</h3>
        <p>Healthcare professionals must hold a <mark>valid license issued by the authority</mark> before practicing within the emirate.</p>
        <p>Applications are reviewed against the <mark>current qualification requirements</mark> in force at the time of submission.</p>
        <div className="home-cite-tag">[1] Exact passage highlighted, not approximated</div>
      </div>
    </div>
  );
}

const SECTIONS = [
  {
    marker: "01",
    eyebrow: "Context before conclusions",
    title: "The right rule\nfor the right place.",
    body: "Every clause is stamped with the authority that issued it. ReguLense keeps DHA, DoH, and MOHAP guidance separated, so an answer never quietly borrows from the wrong jurisdiction.",
    preview: <StampField />,
  },
  {
    marker: "02",
    eyebrow: "Calibrated confidence",
    title: "Confidence you\ncan read on the gauge.",
    body: "Strong evidence gets a direct answer. Weak evidence gets a clear caveat. Below the calibrated floor, ReguLense abstains rather than guess — the gauge is the tell.",
    preview: <GaugeCard />,
  },
  {
    marker: "03",
    eyebrow: "Evidence, not decoration",
    title: "Every answer\nleaves a paper trail.",
    body: "Open the cited passage in its source PDF, with the exact governing line highlighted — not a guessed excerpt, the actual bounding box of the clause.",
    preview: <DocLeaf />,
  },
] as const;

const SECTION_COUNT = SECTIONS.length + 1;

interface Props {
  onComplete: () => void;
}

/** The Home experience: a full-page, vertical-scroll SaaS-style product page --
 * genuinely a separate component/page from the chat and from the second, blocking
 * modal (OnboardingWelcome.tsx) that follows it. Own CSS namespace (`.home-*`) on
 * purpose, so it can never be accidentally merged into the modal again. */
export default function OnboardingHome({ onComplete }: Props) {
  const [visible, setVisible] = useState<boolean[]>(() => Array(SECTION_COUNT).fill(false));
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const sectionRefs = useRef<(HTMLElement | null)[]>([]);

  const complete = useCallback(() => {
    markHomeComplete();
    onComplete();
  }, [onComplete]);

  useEffect(() => {
    rootRef.current?.focus();
  }, []);

  useEffect(() => {
    const revealThreshold = prefersReducedMotion() ? 0 : 0.18;
    const revealObserver = new IntersectionObserver(
      (entries) => {
        const entering = entries.filter((entry) => entry.isIntersecting);
        if (entering.length === 0) return;
        setVisible((prev) => {
          const next = [...prev];
          for (const entry of entering) {
            next[Number((entry.target as HTMLElement).dataset.index)] = true;
          }
          return next;
        });
      },
      { threshold: revealThreshold }
    );
    const activeObserver = new IntersectionObserver(
      (entries) => {
        const mostVisible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (mostVisible) {
          setActive(Number((mostVisible.target as HTMLElement).dataset.index));
        }
      },
      { threshold: 0.5 }
    );
    for (const el of sectionRefs.current) {
      if (!el) continue;
      revealObserver.observe(el);
      activeObserver.observe(el);
    }
    return () => {
      revealObserver.disconnect();
      activeObserver.disconnect();
    };
  }, []);

  const scrollToIndex = (index: number) => {
    const target = sectionRefs.current[index];
    target?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      complete();
    } else if (event.key === "ArrowDown" || event.key === "PageDown") {
      event.preventDefault();
      scrollToIndex(Math.min(active + 1, SECTION_COUNT - 1));
    } else if (event.key === "ArrowUp" || event.key === "PageUp") {
      event.preventDefault();
      scrollToIndex(Math.max(active - 1, 0));
    } else if (event.key === "Home") {
      event.preventDefault();
      scrollToIndex(0);
    } else if (event.key === "End") {
      event.preventDefault();
      scrollToIndex(SECTION_COUNT - 1);
    }
  };

  return (
    <div ref={rootRef} className="home-screen" onKeyDown={handleKeyDown} tabIndex={-1}>
      <header className="home-header">
        <div className="home-brand"><span className="home-brand-mark">R</span><span>ReguLense</span></div>
        <button type="button" className="home-skip" onClick={complete}>Skip introduction</button>
      </header>

      <nav className="home-nav" aria-label="Onboarding sections">
        {Array.from({ length: SECTION_COUNT }, (_, index) => (
          <button
            key={index}
            type="button"
            className={index === active ? "active" : ""}
            aria-label={`Go to section ${index + 1} of ${SECTION_COUNT}`}
            aria-current={index === active}
            onClick={() => scrollToIndex(index)}
          />
        ))}
      </nav>

      <main className="home-scroll">
        <section
          ref={(el) => { sectionRefs.current[0] = el; }}
          data-index={0}
          className="home-section home-hero is-visible"
        >
          <div className="home-hero-glow" aria-hidden="true" />
          <div className="home-copy">
            <p className="home-eyebrow home-rise" style={{ animationDelay: "60ms" }}>Case File No. 001 &mdash; Opened Today</p>
            <h1 className="home-rise" style={{ animationDelay: "140ms" }}>The rule,<br />without <em>the noise.</em></h1>
            <p className="home-body home-rise" style={{ animationDelay: "240ms" }}>
              Ask a question about UAE health regulation. ReguLense opens a case, retrieves the governing
              clause, and hands you a considered answer you can trace back to the exact source line.
            </p>
            <div className="home-authority-row home-rise" style={{ animationDelay: "320ms" }}>
              <div className="home-seal-badge"><div className="home-seal-circle home-seal-dha">DHA</div><span>Dubai</span></div>
              <div className="home-seal-badge"><div className="home-seal-circle home-seal-doh">DoH</div><span>Abu Dhabi</span></div>
              <div className="home-seal-badge"><div className="home-seal-circle home-seal-mohap">MOHAP</div><span>Federal</span></div>
            </div>
            <div className="home-hero-actions home-rise" style={{ animationDelay: "400ms" }}>
              <button type="button" className="home-primary" onClick={() => scrollToIndex(1)}>
                Open the File<span aria-hidden>&rarr;</span>
              </button>
              <button type="button" className="home-secondary" onClick={complete}>Skip introduction</button>
            </div>
          </div>
          <div className="home-visual-wrap home-rise" style={{ animationDelay: "220ms" }}><Ticket /></div>
          <div className="home-scroll-hint" aria-hidden="true"><span>Scroll to explore</span><i>&darr;</i></div>
        </section>

        {SECTIONS.map((item, i) => {
          const index = i + 1;
          const isLast = index === SECTION_COUNT - 1;
          return (
            <div key={item.marker}>
              <div className="home-torn" aria-hidden="true" />
              <section
                ref={(el) => { sectionRefs.current[index] = el; }}
                data-index={index}
                className={`home-section home-content${visible[index] ? " is-visible" : ""}`}
              >
                <div className="home-copy">
                  <div className="home-section-marker"><span>{item.marker}</span><i /></div>
                  <p className="home-eyebrow">{item.eyebrow}</p>
                  <h2>{item.title.split("\n").map((line, li) => <span key={line}>{line}{li === 0 && <br />}</span>)}</h2>
                  <p className="home-body">{item.body}</p>
                  {isLast && (
                    <button type="button" className="home-primary" onClick={complete}>
                      Continue to Chat<span aria-hidden>&rarr;</span>
                    </button>
                  )}
                </div>
                <div className="home-visual-wrap">{item.preview}</div>
              </section>
            </div>
          );
        })}
      </main>
    </div>
  );
}
