import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import AuthorityBadge from "../AuthorityBadge";

describe("AuthorityBadge", () => {
  it("renders a seal chip with 'DHA' for an official Dubai Health Authority document", () => {
    render(<AuthorityBadge authority="Dubai Health Authority" tier="official" />);
    expect(screen.getByText("DHA")).toBeInTheDocument();
  });

  it("renders a seal chip with 'DoH' for an official Department of Health - Abu Dhabi document", () => {
    render(<AuthorityBadge authority="Department of Health - Abu Dhabi" tier="official" />);
    expect(screen.getByText("DoH")).toBeInTheDocument();
  });

  it("falls back to a derived acronym for an unknown official authority", () => {
    render(<AuthorityBadge authority="Sharjah Health Authority" tier="official" />);
    expect(screen.getByText("SHA")).toBeInTheDocument();
  });

  it("renders a dashed 'RESEARCH' chip for a research-tier document", () => {
    render(<AuthorityBadge authority="Asian Journal of Psychiatry" tier="research" />);
    const chip = screen.getByText("RESEARCH");
    expect(chip.getAttribute("style")).toContain("dashed");
  });

  it("never gives a research chip the official seal's box-shadow border", () => {
    render(<AuthorityBadge authority="Asian Journal of Psychiatry" tier="research" />);
    const chip = screen.getByText("RESEARCH");
    expect(chip.getAttribute("style")).not.toContain("box-shadow");
  });
});
