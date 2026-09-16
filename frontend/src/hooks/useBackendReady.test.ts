import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import useBackendReady from "./useBackendReady";

describe("useBackendReady", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    sessionStorage.clear();
    fetchMock = vi.fn(() => Promise.resolve(new Response(null, { status: 503 })));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("keeps polling and recovers after the slow-start window", async () => {
    const { result, unmount } = renderHook(() => useBackendReady());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });

    expect(result.current).toMatchObject({ ready: false, elapsed: 120, failed: true });

    fetchMock.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });

    expect(result.current).toMatchObject({ ready: true, failed: false });
    expect(sessionStorage.getItem("regulense-backend-ready")).toBe("true");

    unmount();
  });
});
