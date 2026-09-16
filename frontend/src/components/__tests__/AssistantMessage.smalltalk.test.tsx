import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AssistantMessage from "../AssistantMessage";
import type { AskResponse, Message } from "../../types/api";

/** The backend's conversational replies (small talk) and safety-screen refusals both
 * arrive as abstentions, and both must render as plain prose with no "report this
 * answer" control -- that control under "Hello!" is noise, and there is no retrieval
 * behind either of them to report on. */

function assistantMessage(response: AskResponse): Message {
  return { id: "m1", role: "assistant", content: "x", timestamp: 0, response };
}

function renderMessage(response: AskResponse) {
  // ReportAnswer (rendered on the ordinary-abstention path) calls useMutation, so the
  // tree needs a real provider rather than a mock of the whole api module.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AssistantMessage
        message={assistantMessage(response)}
        streamingText=""
        steps={[]}
        isStreaming={false}
        question="hi"
        onAskFollowUp={vi.fn()}
        askPending={false}
        provider="openai"
      />
    </QueryClientProvider>,
  );
}

describe("AssistantMessage conversational branch", () => {
  it("renders a small-talk reply with its starters and no report control", () => {
    renderMessage({
      abstained: true,
      smalltalk: true,
      intent: "greeting",
      reason: "Hello. I'm ReguLense.",
      run_id: "run-123",
      suggested_followups: [{ question: "What is the structure of Abu Dhabi health regulations?" }],
    });

    expect(screen.getByText("Hello. I'm ReguLense.")).toBeInTheDocument();
    expect(screen.getByText("Continue exploring")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /structure of Abu Dhabi health regulations/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /report/i })).not.toBeInTheDocument();
    // The legacy abstention frame must not wrap a greeting.
    expect(screen.queryByText(/don't have current guidance/i)).not.toBeInTheDocument();
  });

  it("renders a blocked refusal as prose with no report control", () => {
    renderMessage({
      abstained: true,
      blocked: true,
      reason: "I can't act on instructions that try to change how I work.",
      run_id: "run-123",
    });

    expect(screen.getByText(/can't act on instructions/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /report/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/don't have current guidance/i)).not.toBeInTheDocument();
  });

  it("still frames an ordinary abstention and keeps its report control", () => {
    renderMessage({
      abstained: true,
      reason: "below retrieval confidence threshold",
      run_id: "run-123",
    });

    expect(screen.getByText(/don't have current guidance/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /report/i })).toBeInTheDocument();
  });
});
