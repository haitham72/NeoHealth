import { useMutation, useQuery } from "@tanstack/react-query";
import type {
  AskRequest,
  AskResponse,
  ChatDetail,
  ChatSummary,
  CrossCheckRegulationRequest,
  CrossCheckRegulationResponse,
  DiffFollowupRequest,
  DiffFollowupResponse,
  ReportAnswerRequest,
  ReportAnswerResponse,
  TraceStep,
} from "../types/api";
import { apiUrl } from "./url";

const REQUEST_TIMEOUT_MS = 30_000; // chat completions can legitimately take a few seconds

function isAbortError(e: unknown): e is DOMException {
  return e instanceof DOMException && e.name === "AbortError";
}

export class AskError extends Error {
  reason?: string;
  constructor(message: string, reason?: string) {
    super(message);
    this.reason = reason;
  }
}

/** Error body shape varies by source: HTTPException(detail=str) sends a plain string
 * under "detail"; pydantic validation failures (422s) send
 * "detail" as an array of {msg, loc, ...} objects -- passing that array straight to
 * `new Error(...)` stringifies it via each object's default toString(), producing the
 * useless "[object Object]". slowapi's rate-limit handler uses a different key entirely
 * ("error", not "detail"). */
function extractError(errBody: unknown, fallback: string): AskError {
  const body = errBody as { detail?: unknown; error?: unknown } | null;
  const detail = body?.detail;
  if (typeof detail === "string") return new AskError(detail);
  if (detail && typeof detail === "object" && "message" in detail) {
    const d = detail as { message: unknown; reason?: unknown };
    return new AskError(String(d.message), typeof d.reason === "string" ? d.reason : undefined);
  }
  if (Array.isArray(detail)) {
    const messages = detail.map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d)));
    if (messages.length) return new AskError(messages.join("; "));
  }
  if (typeof body?.error === "string") return new AskError(body.error);
  return new AskError(fallback);
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(apiUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (e) {
    if (isAbortError(e)) throw new Error("Request timed out — the server may be slow or unreachable.");
    throw e;
  } finally {
    clearTimeout(timeoutId);
  }
  if (!res.ok) {
    const errBody = await res.json().catch(() => null);
    throw extractError(errBody, `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function useAskQuestion() {
  return useMutation<AskResponse, AskError, AskRequest>({
    mutationFn: (req: AskRequest) => postJson<AskResponse>("/ask", req),
  });
}

/** On-demand only -- never called automatically alongside useAskQuestion. Given the
 * chunk actually cited in an answer, asks the backend to find the corresponding clause
 * in the previous version (if one exists) and explain what changed. */
export function useDiffFollowup() {
  return useMutation({
    mutationFn: (req: DiffFollowupRequest) => postJson<DiffFollowupResponse>("/diff-followup", req),
  });
}

/** On-demand only, research-tier citations only. Given the chunk actually cited, asks
 * the backend to find related official standard(s) and explain how the research relates
 * to them -- one merged action instead of a separate "find" and "compare" step. */
export function useCrossCheckRegulation() {
  return useMutation({
    mutationFn: (req: CrossCheckRegulationRequest) =>
      postJson<CrossCheckRegulationResponse>("/cross-check-regulation", req),
  });
}

/** On-demand only. Logs user feedback on a specific answer to LangSmith, keyed by
 * the run_id that produced it -- see retrieval.py's _current_run_id() and api.py's
 * /report-answer. Never surfaces as a hard error to the reporting user even if the
 * backend's own LangSmith call failed; callers should check `.success` on the
 * resolved value rather than relying on the mutation's error state. */
export function useReportAnswer() {
  return useMutation({
    mutationFn: (req: ReportAnswerRequest) => postJson<ReportAnswerResponse>("/report-answer", req),
  });
}

/** Live list of chat models currently loaded in LM Studio, for the provider switcher's
 * model dropdown. Polled lazily by react-query — empty array (not an error) means LM
 * Studio isn't running, which the switcher treats as "local unavailable." */
export function useLocalModels() {
  return useQuery({
    queryKey: ["local-models"],
    queryFn: async () => {
      const res = await fetch(apiUrl("/local-models"));
      if (!res.ok) return { models: [] as string[] };
      return res.json() as Promise<{ models: string[] }>;
    },
    staleTime: 30_000,
    retry: false,
  });
}

/** Backs the footer's document/chunk count -- computed live so it can't drift from
 * the actual corpus the way a hardcoded string already did once. */
export function useCorpusStats() {
  return useQuery({
    queryKey: ["corpus-stats"],
    queryFn: async () => {
      const res = await fetch(apiUrl("/corpus-stats"));
      if (!res.ok) throw new Error("failed to load corpus stats");
      return res.json() as Promise<{
        official_documents: number;
        official_chunks: number;
        research_documents: number;
      }>;
    },
    staleTime: 60_000,
    retry: false,
  });
}

/** Per-visitor chat history (list/create/load chats, persist messages). Deliberately
 * separate from useAskQuestion/streamAsk: these are best-effort side calls the caller
 * fires without awaiting on the critical path, so a persistence failure never blocks or
 * breaks asking a question -- same spirit as the backend's enrich_result(). */
export function useChatList(clientId: string) {
  return useQuery({
    queryKey: ["chats", clientId],
    queryFn: async () => {
      const res = await fetch(apiUrl(`/chats?client_id=${encodeURIComponent(clientId)}`));
      if (!res.ok) throw new Error("failed to load chats");
      return res.json() as Promise<ChatSummary[]>;
    },
    staleTime: 10_000,
  });
}

export async function createChat(clientId: string): Promise<ChatSummary> {
  return postJson<ChatSummary>("/chats", { client_id: clientId });
}

export async function getChat(chatId: string, clientId: string): Promise<ChatDetail> {
  const res = await fetch(apiUrl(`/chats/${chatId}?client_id=${encodeURIComponent(clientId)}`));
  if (!res.ok) throw new Error("failed to load chat");
  return res.json() as Promise<ChatDetail>;
}

/** Fire-and-forget: swallows its own errors so a persistence hiccup never surfaces as a
 * chat-breaking error to the user. Callers should not await this on the critical path. */
export async function saveChatMessage(
  chatId: string,
  clientId: string,
  role: "user" | "assistant",
  content: string,
  response?: AskResponse | null
): Promise<void> {
  try {
    await postJson(`/chats/${chatId}/messages`, { client_id: clientId, role, content, response: response ?? null });
  } catch {
    // Chat history is a convenience layer -- never let it break the live conversation.
  }
}

const STREAM_IDLE_TIMEOUT_MS = 60_000; // resets on every frame -- a slow-but-live stream shouldn't be killed

/** Consumes /api/ask-stream's Server-Sent Events, calling onStep for every step along
 * the way and resolving with the final answer once the "done" event arrives. Aborts if
 * no data arrives for STREAM_IDLE_TIMEOUT_MS, rather than hanging forever on a dead
 * connection -- the timer is rearmed on every received frame, not set once up front. */
export async function streamAsk(
  req: AskRequest,
  onStep: (step: TraceStep) => void
): Promise<AskResponse> {
  const controller = new AbortController();
  let idleTimer: ReturnType<typeof setTimeout> = setTimeout(
    () => controller.abort(),
    STREAM_IDLE_TIMEOUT_MS
  );
  const armIdleTimer = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => controller.abort(), STREAM_IDLE_TIMEOUT_MS);
  };

  try {
    let res: Response;
    try {
      res = await fetch(apiUrl("/ask-stream"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
        signal: controller.signal,
      });
    } catch (e) {
      if (isAbortError(e)) throw new Error("Request timed out — the server may be slow or unreachable.");
      throw e;
    }
    if (!res.ok || !res.body) {
      const errBody = await res.json().catch(() => null);
      throw extractError(errBody, `${res.status} ${res.statusText}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      let readResult: ReadableStreamReadResult<Uint8Array>;
      try {
        readResult = await reader.read();
      } catch (e) {
        if (isAbortError(e)) throw new Error("Stream timed out — no data received for a while.");
        throw e;
      }
      armIdleTimer();
      if (readResult.done) break;
      buffer += decoder.decode(readResult.value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const line = frame.trim();
        if (!line.startsWith("data:")) continue;
        const event = JSON.parse(line.slice(5).trim());
        if (event.step === "error")
          throw new AskError(event.detail, typeof event.reason === "string" ? event.reason : undefined);
        if (event.step === "done") return event.result as AskResponse;
        onStep(event as TraceStep);
      }
    }
    throw new Error("Stream ended before a result arrived");
  } finally {
    clearTimeout(idleTimer);
  }
}
