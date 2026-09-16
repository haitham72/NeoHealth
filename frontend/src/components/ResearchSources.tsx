import type { RetrievedChunk } from "../types/api";

interface Props {
  chunks: RetrievedChunk[];
}

export default function ResearchSources({ chunks }: Props) {
  if (!chunks.length) return null;

  return (
    <div className="mt-4 pt-4" style={{ borderTop: "1px solid var(--rule)" }}>
      <h3
        className="text-[11px] font-semibold tracking-[0.1em] uppercase mb-2"
        style={{ color: "var(--ink-faint)", fontFamily: "var(--font-display)" }}
      >
        Additional research sources
      </h3>
      <ul className="space-y-1">
        {chunks.map((chunk) => (
          <li key={chunk.chunk_id} className="text-[13px]" style={{ color: "var(--ink-dim)", fontFamily: "var(--font-display)" }}>
            → {chunk.document?.title} · p.{chunk.page}
          </li>
        ))}
      </ul>
    </div>
  );
}
