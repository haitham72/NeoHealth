import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OnboardingWelcome from "../OnboardingWelcome";

describe("OnboardingWelcome", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sessionStorage.clear();
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockReturnValue({ matches: true }),
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    sessionStorage.clear();
  });

  it("does not complete the three-step onboarding before 22 seconds", async () => {
    const onComplete = vi.fn();
    render(<OnboardingWelcome onComplete={onComplete} />);

    fireEvent.click(screen.getByRole("button", { name: /continue/i }));
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    const beginButton = screen.getByRole("button", { name: /begin your case/i });
    expect(beginButton).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    expect(onComplete).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(21_999);
    });

    expect(beginButton).toBeDisabled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    expect(beginButton).toBeEnabled();

    fireEvent.click(beginButton);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });
});
