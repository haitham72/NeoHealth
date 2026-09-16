import type { ReactNode } from "react";

const CITATION_PATTERN = /\[(\d+)\]/g;

/** Structural subset of RetrievedChunk that citations.tsx actually needs -- avoids
 * a circular import between lib/ and types/. */
export interface RetrievedChunkLike {
  chunk_id: number;
}

/** Splits a plain text string (one markdown leaf, e.g. a paragraph or list item's
 * flattened text) on inline "[N]" citation markers, replacing each with a
 * clickable superscript button. N is 1-indexed to match the numbering the model
 * was given in the prompt (see retrieval.py's _build_messages), which in turn
 * matches `chunks`' array order exactly (both are built from the same
 * filtered/numbered list) -- so `chunks[N - 1]` is always the right chunk. An
 * out-of-range N (the model inventing a number) is rendered as plain text
 * instead of crashing or opening a broken popover. */
export function renderWithCitations(
  text: string,
  chunks: RetrievedChunkLike[],
  onOpen: (index: number) => void
): ReactNode[] {
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  CITATION_PATTERN.lastIndex = 0;
  while ((match = CITATION_PATTERN.exec(text)) !== null) {
    if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index));
    const n = parseInt(match[1], 10);
    if (n >= 1 && n <= chunks.length) {
      parts.push(
        <button
          key={`cite-${key++}`}
          type="button"
          onClick={() => onOpen(n - 1)}
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            minWidth: "16px",
            height: "16px",
            padding: "0 3px",
            marginLeft: "1px",
            fontSize: "10px",
            fontWeight: 700,
            borderRadius: "4px",
            background: "var(--fhir-blue-light)",
            color: "var(--fhir-dark)",
            verticalAlign: "super",
            lineHeight: 1,
          }}
          aria-label={`Source ${n}`}
        >
          {n}
        </button>
      );
    } else {
      parts.push(match[0]);
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}
