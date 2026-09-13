import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import CacheNotice from "../CacheNotice";

const h = vi.hoisted(() => ({
  state: { isPending: false, isSuccess: false },
  mutate: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  useEvictCache: () => ({ ...h.state, mutate: h.mutate }),
}));

describe("CacheNotice", () => {
  beforeEach(() => {
    h.state = { isPending: false, isSuccess: false };
    h.mutate.mockClear();
  });

  it("renders the note and a remove control when a signed token is present", () => {
    render(<CacheNotice token="signed.token" />);

    expect(screen.getByText("Served from cache")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /remove from cache/i })).toBeInTheDocument();
  });

  it("renders no remove control when the token is missing", () => {
    render(<CacheNotice />);

    expect(screen.getByText("Served from cache")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("sends the token to the evict mutation on click", () => {
    render(<CacheNotice token="signed.token" />);

    fireEvent.click(screen.getByRole("button", { name: /remove from cache/i }));

    expect(h.mutate).toHaveBeenCalledWith("signed.token");
  });

  it("swaps to the removed state after a successful eviction", () => {
    h.state = { isPending: false, isSuccess: true };

    render(<CacheNotice token="signed.token" />);

    expect(screen.getByText("Removed from cache")).toBeInTheDocument();
    expect(screen.queryByText("Served from cache")).toBeNull();
  });
});
