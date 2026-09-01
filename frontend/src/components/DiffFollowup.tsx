import { useDiffFollowup } from "../api/client";

interface Props {
  docCode: string;
  currentDocumentId: number;
  citedText: string;
  citedPage: number;
  question: string;
}

/* On-demand follow-up, never fetched automatically: "See what changed" only fires when
   the user asks for it, matching the same abstain-unless-asked principle as the rest of
   the answer pipeline -- no extra latency or cost on every query. */
export default function DiffFollowup({ docCode, currentDocumentId, citedText, citedPage, question }: Props) {
  const mutation = useDiffFollowup();

  if (!mutation.data && !mutation.isPending && !mutation.isError) {
    return (
      <div className="mt-4 flex justify-end">
        <button
          type="button"
          onClick={() =>
            mutation.mutate({
              doc_code: docCode,
              current_document_id: currentDocumentId,
              cited_text: citedText,
              cited_page: citedPage,
              question,
            })
          }
          className="inline-flex items-center gap-2 rounded-md px-4 py-2.5 text-[12px] font-semibold tracking-[0.04em] uppercase transition-colors"
          style={{
            background: "var(--verified-brass)",
            color: "var(--surface)",
            fontFamily: "var(--font-display)",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.filter = "brightness(0.92)")}
          onMouseLeave={(e) => (e.currentTarget.style.filter = "none")}
        >
          <span aria-hidden style={{ fontSize: "14px", lineHeight: 1 }}>
            ⇄
          </span>
          See what changed
        </button>
      </div>
    );
  }

  return (
    <div
      className="mt-3 rounded-md p-4"
      style={{ background: "var(--surface-sunken, rgba(0,0,0,0.02))", border: "1px solid var(--rule)" }}
    >
      <div
        className="text-[11px] font-semibold tracking-[0.1em] uppercase mb-2"
        style={{ color: "var(--ink-faint)", fontFamily: "var(--font-display)" }}
      >
        What changed
      </div>

      {mutation.isPending && (
        <div className="text-[13px]" style={{ color: "var(--ink-faint)" }}>
          Reading the full current and previous versions to compare...
        </div>
      )}

      {mutation.isError && (
        <div className="text-[13px]" style={{ color: "var(--superseded-rust)" }}>
          {(mutation.error as Error).message}
        </div>
      )}

      {mutation.data && !mutation.data.available && (
        <div className="text-[13px]" style={{ color: "var(--ink-faint)" }}>
          {mutation.data.reason}
        </div>
      )}

      {mutation.data && mutation.data.available && (
        <div>
          <p className="text-[14px] leading-relaxed" style={{ color: "var(--ink)", fontFamily: "var(--font-body)" }}>
            {mutation.data.explanation}
          </p>
          <p className="mt-2 text-[11px]" style={{ color: "var(--ink-faint)" }}>
            Compared full text against v{mutation.data.previous_version} &middot; {mutation.data.previous_effective_date}
          </p>
        </div>
      )}
    </div>
  );
}
