import { useState } from "react";
import AuthorityBadge from "./AuthorityBadge";
import DiffFollowup from "./DiffFollowup";
import CrossCheckRegulation from "./CrossCheckRegulation";
import PdfOverlay from "./PdfOverlay";
import type { RetrievedChunk } from "../types/api";

interface Props {
  chunk: RetrievedChunk;
  index: number;
  /** Every source used in the answer, in citation order -- passed through to
   * PdfOverlay so its Prev/Next can continue into adjacent sources, not just page
   * within this one citation. */
  sources: RetrievedChunk[];
  question: string;
  onClose: () => void;
}

/** Slide-up detail card for one numbered citation. Hosts both existing
 * follow-up actions unchanged: DiffFollowup (any document with a superseded
 * sibling) and CrossCheckRegulation (research-tier documents only) -- neither
 * component nor its backend endpoint changed, only where it's rendered. */
export default function CitationPopover({ chunk, index, sources, question, onClose }: Props) {
  const doc = chunk.document;
  const [pdfOpen, setPdfOpen] = useState(false);
  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true">
      <button aria-label="Close" className="absolute inset-0 cursor-default" style={{ background: "rgba(2,48,71,0.4)" }} onClick={onClose} />
      <div
        className="absolute bottom-0 left-0 right-0 mx-auto max-w-[520px] rounded-t-lg p-5 sm:bottom-8 sm:rounded-lg"
        style={{ background: "var(--fhir-surface)", boxShadow: "0 -8px 32px rgba(0,0,0,0.2)" }}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--fhir-blue)" }}>
              Source {index + 1}
            </div>
            <div className="text-[15px] font-semibold" style={{ color: "var(--fhir-dark)" }}>
              {doc?.title ?? "Source document"}
            </div>
          </div>
          {doc && <AuthorityBadge authority={doc.authority} tier={doc.tier} />}
          <button type="button" onClick={onClose} className="text-[13px]" style={{ color: "var(--ink-dim)" }}>
            Close
          </button>
        </div>

        {doc && (
          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-[12px]">
            <div><dt style={{ color: "var(--ink-faint)" }}>Code</dt><dd>{doc.doc_code}</dd></div>
            <div><dt style={{ color: "var(--ink-faint)" }}>Version</dt><dd>{doc.version}</dd></div>
            <div><dt style={{ color: "var(--ink-faint)" }}>Effective</dt><dd>{doc.effective_date}</dd></div>
            <div><dt style={{ color: "var(--ink-faint)" }}>Page</dt><dd>{chunk.page}</dd></div>
          </dl>
        )}

        <p className="mt-3 text-[13px] leading-relaxed" style={{ color: "var(--ink-dim)" }}>
          {chunk.text.length > 320 ? chunk.text.slice(0, 320).trimEnd() + "…" : chunk.text}
        </p>

        {doc && (
          <button
            type="button"
            onClick={() => setPdfOpen(true)}
            className="mt-2 text-[12px] font-semibold"
            style={{ color: "var(--fhir-blue)" }}
          >
            View in PDF &#8599;
          </button>
        )}

        {doc && (
          <DiffFollowup docCode={doc.doc_code} currentDocumentId={doc.id} citedText={chunk.text} citedPage={chunk.page} question={question} />
        )}
        {doc?.tier === "research" && (
          <CrossCheckRegulation docCode={doc.doc_code} currentDocumentId={doc.id} citedText={chunk.text} citedPage={chunk.page} question={question} />
        )}
      </div>

      {pdfOpen && <PdfOverlay sources={sources} startIndex={index} onClose={() => setPdfOpen(false)} />}
    </div>
  );
}
