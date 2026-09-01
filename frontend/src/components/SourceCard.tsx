import type { RetrievedChunk } from "../types/api";

interface Props {
  chunks: RetrievedChunk[];
  onOpen: (index: number) => void;
}

export default function SourceCard({ chunks, onOpen }: Props) {
  if (!chunks.length) return null;
  return (
    <div className="mt-2 rounded-md p-2.5" style={{ background: "var(--fhir-bg)", border: "1px solid var(--rule)" }}>
      <div className="text-[10px] font-semibold uppercase tracking-[0.08em] mb-1.5" style={{ color: "var(--ink-faint)" }}>
        Sources
      </div>
      <ul className="flex flex-col gap-1">
        {chunks.map((chunk, i) => (
          <li key={chunk.chunk_id}>
            <button
              type="button"
              onClick={() => onOpen(i)}
              className="flex items-center gap-2 text-left text-[12px] hover:underline"
              style={{ color: "var(--fhir-blue)" }}
            >
              <span
                className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded text-[10px] font-bold"
                style={{ background: "var(--fhir-blue-light)", color: "var(--fhir-dark)" }}
              >
                {i + 1}
              </span>
              {chunk.document?.doc_code ?? "Source document"} — {chunk.document?.title}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
