import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import ResearchSources from "../ResearchSources";
import type { RetrievedChunk } from "../../types/api";

function chunk(overrides: Partial<RetrievedChunk> = {}): RetrievedChunk {
  return {
    chunk_id: 1,
    document_id: 10,
    page: 5,
    page_end: 5,
    heading_path: [],
    bboxes: [],
    text: "Telepsychiatry adoption before COVID-19.",
    semantic_score: 0.4,
    rrf: 0.03,
    used_for_answer: false,
    document: {
      id: 10,
      title: "Telepsychiatry in the Arab World",
      doc_code: "RESEARCH/ELHAYEK-01",
      version: "1",
      effective_date: "2021-06-01",
      authority: "Asian Journal of Psychiatry",
      source_url: null,
      superseded: false,
      tier: "research",
    },
    ...overrides,
  };
}

describe("ResearchSources", () => {
  it("renders a list of research chunks", () => {
    render(<ResearchSources chunks={[chunk()]} />);
    expect(screen.getByText("Additional research sources")).toBeInTheDocument();
  });

  it("returns null and renders nothing when there are no research chunks", () => {
    const { container } = render(<ResearchSources chunks={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows each chunk's document title and page number", () => {
    render(<ResearchSources chunks={[chunk()]} />);
    expect(screen.getByText(/Telepsychiatry in the Arab World/)).toBeInTheDocument();
    expect(screen.getByText(/p\.5/)).toBeInTheDocument();
  });
});
