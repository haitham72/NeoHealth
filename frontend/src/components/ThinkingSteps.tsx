import type { TraceStep } from "../types/api";

const STEP_LABELS: Record<string, (s: TraceStep) => string> = {
  embedding_query: () => "Embedding question",
  searching_sources: () => "Searching sources",
  aggregating_results: (s) => `Aggregating results${s.detail ? ` — ${s.detail}` : ""}`,
  checking_supersession: (s) => `Checking supersession${s.doc_code ? ` for ${s.doc_code}` : ""}`,
  citing_source: (s) => `Citing: ${[s.doc_code, s.title].filter(Boolean).join(" — ")}`,
  generating_answer: (s) => `Generating (${s.detail ?? "chat model"})`,
  provider_fallback: (s) => s.detail ?? "Switching provider",
  waiting_for_backend: (s) => `Starting the server — ${s.detail ?? "0"}s`,
  backend_unavailable: () => "The server did not start. Try again later.",
};

function labelFor(step: TraceStep): string {
  return STEP_LABELS[step.step]?.(step) ?? step.step;
}

interface Props {
  steps: TraceStep[];
  active: boolean;
}

export default function ThinkingSteps({ steps, active }: Props) {
  if (!steps.length && !active) return null;
  return (
    <div className="mb-2 flex flex-col gap-1 text-[11px]" style={{ color: "var(--ink-faint)" }}>
      {steps.map((step, i) =>
        step.step === "provider_fallback" ? (
          <div key={i} className="font-semibold" style={{ color: "var(--ink-dim)" }}>
            {labelFor(step)}
          </div>
        ) : (
          <div key={i}>{labelFor(step)}</div>
        )
      )}
      {active && <div className="animate-pulse">thinking…</div>}
    </div>
  );
}
