import { useEffect, useState } from "react";
import { apiUrl } from "../api/url";

const READY_STORAGE_KEY = "regulense-backend-ready";
const POLL_INTERVAL_MS = 5_000;
const REQUEST_TIMEOUT_MS = 4_500;
const MAX_WAIT_MS = 120_000;

interface BackendReadyState {
  ready: boolean;
  elapsed: number;
  failed: boolean;
}

function wasReadyThisSession(): boolean {
  try {
    return sessionStorage.getItem(READY_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function cacheReady(): void {
  try {
    sessionStorage.setItem(READY_STORAGE_KEY, "true");
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
}

/** Polls the DB-backed readiness endpoint without stacking requests during boot. */
export function useBackendReady(): BackendReadyState {
  const [state, setState] = useState<BackendReadyState>(() => ({
    ready: wasReadyThisSession(),
    elapsed: 0,
    failed: false,
  }));

  useEffect(() => {
    if (state.ready || state.failed) return;

    const startedAt = Date.now();
    let stopped = false;
    let inFlight = false;
    let controller: AbortController | null = null;

    const elapsedSeconds = () => Math.min(120, Math.floor((Date.now() - startedAt) / 1000));

    const stop = () => {
      stopped = true;
      controller?.abort();
    };

    const fail = () => {
      if (stopped) return;
      stop();
      setState({ ready: false, elapsed: 120, failed: true });
    };

    const poll = async () => {
      if (stopped || inFlight) return;
      if (Date.now() - startedAt >= MAX_WAIT_MS) {
        fail();
        return;
      }

      inFlight = true;
      controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller?.abort(), REQUEST_TIMEOUT_MS);

      try {
        const response = await fetch(apiUrl("/ready"), { signal: controller.signal });
        if (!stopped && Date.now() - startedAt < MAX_WAIT_MS && response.status === 200) {
          cacheReady();
          setState({ ready: true, elapsed: elapsedSeconds(), failed: false });
          stop();
        }
      } catch {
        // Connection failures and aborts are expected while Render is booting.
      } finally {
        window.clearTimeout(timeoutId);
        controller = null;
        inFlight = false;
      }
    };

    const pollInterval = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    const elapsedInterval = window.setInterval(() => {
      const elapsed = elapsedSeconds();
      if (elapsed >= MAX_WAIT_MS / 1000) {
        fail();
      } else if (!stopped) {
        setState((current) => ({ ...current, elapsed }));
      }
    }, 1_000);

    // The first poll is also the Render wake-up request.
    void poll();

    return () => {
      stop();
      window.clearInterval(pollInterval);
      window.clearInterval(elapsedInterval);
    };
  }, [state.failed, state.ready]);

  return state;
}

export default useBackendReady;
