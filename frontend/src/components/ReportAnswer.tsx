import { useState } from "react";
import { useReportAnswer } from "../api/client";
import type { ReportReason } from "../types/api";

interface Props {
  runId: string;
  variant: "answered" | "abstained";
}

const REASONS_BY_VARIANT: Record<Props["variant"], { value: ReportReason; label: string }[]> = {
  answered: [
    { value: "wrong_citation", label: "Wrong/mixed citation" },
    { value: "unrelated", label: "Unrelated to my question" },
    { value: "other", label: "Other" },
  ],
  abstained: [
    { value: "incorrect_abstention", label: "Should have had an answer" },
    { value: "other", label: "Other" },
  ],
};

/** On-demand only, never fetched automatically. Contextual reason set: an answered
 * message can't sensibly be reported as "incorrectly abstained", and an abstention
 * has no citation to call "wrong". Renders nothing if runId is falsy -- matches the
 * rest of the LangSmith integration's "harmless no-op when tracing is off" contract
 * (see retrieval.py's _current_run_id()). */
export default function ReportAnswer({ runId, variant }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [selectedReason, setSelectedReason] = useState<ReportReason | null>(null);
  const [comment, setComment] = useState("");
  const mutation = useReportAnswer();

  if (mutation.isSuccess && mutation.data.success) {
    return (
      <div className="mt-2 text-[11px]" style={{ color: "var(--ink-faint)" }}>
        Thanks — reported.
      </div>
    );
  }

  const submit = (reason: ReportReason) => {
    setSelectedReason(reason);
    if (reason === "other") return; // wait for optional comment + explicit submit
    mutation.mutate({ run_id: runId, reason });
  };

  if (!expanded) {
    return (
      <button type="button" onClick={() => setExpanded(true)} className="mt-2 text-[11px] underline" style={{ color: "var(--ink-faint)" }}>
        Report an issue
      </button>
    );
  }

  return (
    <div className="mt-2 flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5">
        {REASONS_BY_VARIANT[variant].map((r) => (
          <button
            key={r.value}
            type="button"
            onClick={() => submit(r.value)}
            disabled={mutation.isPending}
            className="rounded-full px-2.5 py-1 text-[11px] disabled:opacity-50"
            style={{
              border: `1px solid ${selectedReason === r.value ? "var(--fhir-blue)" : "var(--rule)"}`,
              color: selectedReason === r.value ? "var(--fhir-blue)" : "var(--ink-dim)",
            }}
          >
            {r.label}
          </button>
        ))}
      </div>

      {selectedReason === "other" && (
        <div className="flex flex-col gap-1.5">
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Optional: what went wrong?"
            rows={2}
            className="w-full rounded-md px-2 py-1.5 text-[12px] outline-none"
            style={{ border: "1px solid var(--rule)" }}
          />
          <button
            type="button"
            onClick={() => mutation.mutate({ run_id: runId, reason: "other", comment: comment || undefined })}
            disabled={mutation.isPending}
            className="self-start rounded-md px-3 py-1.5 text-[11px] font-semibold disabled:opacity-50"
            style={{ background: "var(--fhir-blue)", color: "#fff" }}
          >
            Submit
          </button>
        </div>
      )}

      {(mutation.isError || (mutation.isSuccess && !mutation.data.success)) && (
        <div className="text-[11px]" style={{ color: "var(--superseded-rust)" }}>
          Couldn't submit — try again.
        </div>
      )}
    </div>
  );
}
