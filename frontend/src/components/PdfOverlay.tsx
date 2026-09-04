import { useEffect, useMemo, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist/types/src/display/api";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { RetrievedChunk } from "../types/api";
import { apiUrl } from "../api/url";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerSrc;

interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

const RENDER_SCALE = 1.5;

/** Open PDF in new tab by fetching as blob and creating object URL. */
async function openInNewTab(documentId: number, page: number) {
  const res = await fetch(apiUrl(`/pdf/${documentId}`));
  if (!res.ok) return;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  window.open(`${url}#page=${page}`, "_blank", "noopener,noreferrer");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** Distinct pages a source's bboxes actually land on, in order -- the sequence Prev/
 * Next steps through for that one source. */
function citedPagesFor(source: RetrievedChunk): number[] {
  return [...new Set(source.bboxes.map((b) => b.page_no))].sort((a, b) => a - b);
}

interface Props {
  /** Every source used to generate the current answer, in citation order ([1], [2], ...).
   * Next/Prev walk this whole list, not just the one citation that was clicked --
   * stepping off either end of the current source's cited pages continues into the
   * adjacent source instead of stopping. */
  sources: RetrievedChunk[];
  startIndex: number;
  onClose: () => void;
}

/* Highlighting used to be a client-side heuristic: search the PDF's text layer for
   whatever lines best word-overlapped the whole generated answer, inside a hardcoded
   3-line window. That routinely cut off mid-sentence, because it had no idea where the
   actually-cited chunk began or ended. Chunk provenance (page + bounding box per
   original PDF element) is now computed once at ingest time by Docling and stored --
   this component just renders those exact rectangles. No text search, no guessing. */
export default function PdfOverlay({ sources, startIndex, onClose }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const firstHighlightRef = useRef<HTMLDivElement>(null);
  const pdfRef = useRef<PDFDocumentProxy | null>(null);

  const [sourceIndex, setSourceIndex] = useState(startIndex);
  const source = sources[sourceIndex];
  const documentId = source.document_id;
  const bboxes = source.bboxes;
  const title = source.document?.title ?? "Source document";

  const [page, setPage] = useState(source.page);
  const [numPages, setNumPages] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [highlights, setHighlights] = useState<Box[]>([]);

  // Distinct pages that carry at least one highlight for the CURRENT source, in order.
  // Multiple boxes on the SAME page are one stop, not several -- they already all
  // render together the moment that page is showing, so stepping "Next" between two
  // boxes already visible on screen would look like nothing happened.
  const highlightPages = useMemo(() => citedPagesFor(source), [source]);
  const hasMultipleHighlightPages = highlightPages.length > 1 || sources.length > 1;
  const highlightPageIndex = highlightPages.indexOf(page);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      const pdf = await pdfjsLib.getDocument({
        url: apiUrl(`/pdf/${documentId}`),
      }).promise;
      if (cancelled) return;
      pdfRef.current = pdf;
      setNumPages(pdf.numPages);
      await renderPage(pdf, page);
    })()
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // documentId only — page changes are handled by the effect below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId]);

  useEffect(() => {
    if (!pdfRef.current) return;
    let cancelled = false;
    setLoading(true);
    renderPage(pdfRef.current, page).finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, bboxes]);

  // Scroll the first highlight on whatever page just rendered into view -- covers the
  // initial mount, a same-source page jump, and a cross-source switch uniformly, since
  // all three just change `highlights` via the effect above.
  useEffect(() => {
    if (highlights.length > 0) {
      requestAnimationFrame(() => {
        firstHighlightRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
      });
    }
  }, [highlights]);

  async function renderPage(pdf: PDFDocumentProxy, pageNum: number) {
    const pdfPage = await pdf.getPage(pageNum);
    const viewport = pdfPage.getViewport({ scale: RENDER_SCALE });
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    await pdfPage.render({ canvasContext: ctx, viewport, canvas }).promise;

    // bboxes are normalized (0-1, top-left origin) at ingest time -- just scale by
    // however big this page happens to be rendered right now.
    const boxes = bboxes
      .filter((b) => b.page_no === pageNum)
      .map((b) => ({
        left: b.l * viewport.width,
        top: b.t * viewport.height,
        width: (b.r - b.l) * viewport.width,
        height: (b.b - b.t) * viewport.height,
      }));
    setHighlights(boxes);
  }

  // Prev/Next only ever move between cited pages -- never onto an uncited one. Within
  // the current source that means stepping through its own cited pages; past either
  // end, they continue into the adjacent source (backward lands on its LAST cited
  // page, forward on its FIRST) so "Next" reads as one continuous walk through every
  // source used in the answer, in citation order.
  const canPrev = highlightPageIndex > 0 || sourceIndex > 0;
  const canNext = highlightPageIndex < highlightPages.length - 1 || sourceIndex < sources.length - 1;

  const goPrev = () => {
    if (highlightPageIndex > 0) {
      setPage(highlightPages[highlightPageIndex - 1]);
    } else if (sourceIndex > 0) {
      const prevPages = citedPagesFor(sources[sourceIndex - 1]);
      setSourceIndex(sourceIndex - 1);
      setPage(prevPages[prevPages.length - 1] ?? sources[sourceIndex - 1].page);
    }
  };
  const goNext = () => {
    if (highlightPageIndex < highlightPages.length - 1) {
      setPage(highlightPages[highlightPageIndex + 1]);
    } else if (sourceIndex < sources.length - 1) {
      const nextPages = citedPagesFor(sources[sourceIndex + 1]);
      setSourceIndex(sourceIndex + 1);
      setPage(nextPages[0] ?? sources[sourceIndex + 1].page);
    }
  };

  const spansPages = source.page_end !== source.page;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-4 sm:p-8"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <button
        aria-label="Close PDF viewer"
        className="absolute inset-0 cursor-default"
        style={{ background: "rgba(20, 32, 58, 0.55)" }}
        onClick={onClose}
      />
      <div
        className="relative flex h-full w-full max-w-4xl flex-col overflow-hidden rounded-md"
        style={{ background: "var(--surface)", boxShadow: "var(--shadow-card)" }}
      >
        <div
          className="flex items-center justify-between gap-3 px-4 py-3"
          style={{ borderBottom: "1px solid var(--rule)" }}
        >
          <div className="min-w-0">
            <p
              className="truncate text-[13px] font-semibold"
              style={{ color: "var(--ink)", fontFamily: "var(--font-display)" }}
            >
              {title}
            </p>
            {(sources.length > 1 || spansPages) && (
              <p
                className="text-[11px]"
                style={{ color: "var(--ink-faint)", fontFamily: "var(--font-display)" }}
              >
                {sources.length > 1 ? `Source ${sourceIndex + 1} of ${sources.length}` : ""}
                {sources.length > 1 && spansPages ? " · " : ""}
                {spansPages ? `cited passage spans pages ${source.page}–${source.page_end}` : ""}
              </p>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <button
              type="button"
              disabled={!canPrev}
              onClick={goPrev}
              className="text-[12px] disabled:opacity-30"
              style={{ color: "var(--authority-navy)", fontFamily: "var(--font-display)" }}
              title={hasMultipleHighlightPages ? "Previous cited page" : undefined}
            >
              &larr; Prev
            </button>
            <span
              className="text-[12px] tabular-nums"
              style={{ color: "var(--ink-dim)", fontFamily: "var(--font-display)" }}
            >
              Page {page}
              {numPages ? ` / ${numPages}` : ""}
              {highlightPages.length > 1 ? ` · cited page ${highlightPageIndex + 1}/${highlightPages.length}` : ""}
            </span>
            <button
              type="button"
              disabled={!canNext}
              onClick={goNext}
              className="text-[12px] disabled:opacity-30"
              style={{ color: "var(--authority-navy)", fontFamily: "var(--font-display)" }}
              title={hasMultipleHighlightPages ? "Next cited page" : undefined}
            >
              Next &rarr;
            </button>
            <button
              type="button"
              onClick={() => void openInNewTab(documentId, page)}
              className="text-[12px] font-semibold"
              style={{ color: "var(--authority-navy)", fontFamily: "var(--font-display)" }}
            >
              Open in new tab &#8599;
            </button>
            <button
              type="button"
              onClick={onClose}
              className="text-[13px]"
              style={{ color: "var(--ink-dim)", fontFamily: "var(--font-display)" }}
            >
              Close
            </button>
          </div>
        </div>

        <div
          className="relative flex flex-1 justify-center overflow-auto"
          style={{ background: "var(--paper)" }}
        >
          {loading && (
            <p
              className="absolute left-1/2 top-6 -translate-x-1/2 text-[13px]"
              style={{ color: "var(--ink-dim)", fontFamily: "var(--font-display)" }}
            >
              Loading page&hellip;
            </p>
          )}
          {error && (
            <p
              className="absolute left-1/2 top-6 -translate-x-1/2 px-6 text-center text-[13px]"
              style={{ color: "var(--superseded-rust)", fontFamily: "var(--font-display)" }}
            >
              {error}
            </p>
          )}
          <div className="relative my-4 h-fit">
            <canvas ref={canvasRef} />
            <div className="pointer-events-none absolute inset-0">
              {highlights.map((box, i) => (
                <div
                  key={i}
                  ref={i === 0 ? firstHighlightRef : undefined}
                  style={{
                    position: "absolute",
                    left: box.left,
                    top: box.top,
                    width: box.width,
                    height: box.height,
                    background: "rgba(150, 119, 46, 0.30)",
                    borderRadius: 2,
                  }}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
