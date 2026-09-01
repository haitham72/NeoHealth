import { useCrossCheckRegulation } from "../api/client";
import { getOfficialAuthorityStyle } from "./AuthorityBadge";

interface Props {
  docCode: string;
  currentDocumentId: number;
  citedText: string;
  citedPage: number;
  question: string;
}

/* On-demand only, research-tier citations only: a research paper is persuasive but
   non-binding, so this answers "is there an actual regulation behind this?" in one
   merged action (find + explain) rather than two separate buttons. Uses --authority-navy,
   not --verified-brass -- brass is reserved for "verified/in force" status (see
   VersionLedger), and this button hasn't verified anything until clicked. */
export default function CrossCheckRegulation({
  docCode,
  currentDocumentId,
  citedText,
  citedPage,
  question,
}: Props) {
  const mutation = useCrossCheckRegulation();

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
            background: "var(--authority-navy)",
            color: "var(--surface)",
            fontFamily: "var(--font-display)",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--authority-navy-hover)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "var(--authority-navy)")}
        >
          <span aria-hidden style={{ fontSize: "14px", lineHeight: 1 }}>
            ⚖︎
          </span>
          Cross-check regulation
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
        Regulatory cross-check
      </div>

      {mutation.isPending && (
        <div className="text-[13px]" style={{ color: "var(--ink-faint)" }}>
          Searching official standards and comparing...
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
          <div className="mt-3 flex flex-col gap-1.5">
            {mutation.data.documents.map((doc) => {
              const { code, color, bg } = getOfficialAuthorityStyle(doc.authority);
              return (
                <div
                  key={doc.doc_code}
                  className="flex items-center gap-2 rounded px-2.5 py-1.5"
                  style={{ background: "var(--surface)", border: "1px solid var(--rule)" }}
                >
                  <span
                    className="rounded-sm px-1.5 py-0.5 text-[11px] font-bold tracking-[0.04em]"
                    style={{ color, background: bg, fontFamily: "var(--font-display)" }}
                  >
                    {code}
                  </span>
                  <span className="text-[12.5px]" style={{ color: "var(--ink)", fontFamily: "var(--font-display)" }}>
                    {doc.title}
                  </span>
                  <span
                    className="ml-auto text-[11px]"
                    style={{ color: "var(--ink-faint)", fontFamily: "var(--font-display)" }}
                  >
                    v{doc.version}
                  </span>
                </div>
              );
            })}
          </div>
          <p className="mt-2 text-[11px]" style={{ color: "var(--ink-faint)" }}>
            Cross-checked against {mutation.data.documents.length} official standard
            {mutation.data.documents.length !== 1 ? "s" : ""}
          </p>
        </div>
      )}
    </div>
  );
}
