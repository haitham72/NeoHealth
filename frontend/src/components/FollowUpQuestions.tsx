interface Props {
  questions: string[];
  onAsk: (question: string) => void;
  /** True while a question is already in flight -- the app only ever runs one /ask at
   * a time, so these need to disable during it rather than let repeated clicks queue
   * up multiple concurrent requests. */
  disabled: boolean;
}

/** "Continue exploring" panel shown under a successful answer -- visually distinct from
 * the horizontal example-question pills in ChatInput (a vertical stack of left-aligned
 * rows, set off by a rule), so it reads as a dedicated part of the answer rather than a
 * repeat of the empty-state prompts. */
export default function FollowUpQuestions({ questions, onAsk, disabled }: Props) {
  if (questions.length === 0) return null;

  return (
    <div className="mt-3 border-t pt-3" style={{ borderColor: "var(--rule)" }}>
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.06em]" style={{ color: "var(--ink-dim)" }}>
        Continue exploring
      </div>
      <div className="flex flex-col gap-1.5">
        {questions.map((q) => (
          <button
            key={q}
            type="button"
            disabled={disabled}
            onClick={() => onAsk(q)}
            className="flex items-start gap-2 rounded-md px-3 py-2 text-left text-[13px] leading-snug transition-colors hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:brightness-100"
            style={{ background: "var(--fhir-bg)", border: "1px solid var(--rule)", color: "var(--ink)" }}
          >
            <span aria-hidden style={{ color: "var(--fhir-blue)" }}>
              →
            </span>
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
