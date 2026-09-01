import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import AppShell from "./components/AppShell";
import ChatHeader from "./components/ChatHeader";
import ChatInput from "./components/ChatInput";
import MessageList from "./components/MessageList";
import UserMessage from "./components/UserMessage";
import AssistantMessage from "./components/AssistantMessage";
import { AskError, streamAsk } from "./api/client";
import type { Answered, AskRequest, AskResponse, Message, TraceStep } from "./types/api";

const MAX_HISTORY = 10;

function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [steps, setSteps] = useState<TraceStep[]>([]);
  const [streamingText, setStreamingText] = useState("");
  const [pendingQuestion, setPendingQuestion] = useState("");
  const [showWaitWarning, setShowWaitWarning] = useState(false);
  // A ref, not state -- state's disabled-button re-render can lag behind a burst of
  // rapid clicks (each dispatched as its own synchronous event before React repaints),
  // letting several through before the button visually disables. A ref updates
  // immediately, so even the second click in the same burst sees it.
  const askInFlightRef = useRef(false);
  // Remembers the filters from the most recent ask() call, so a "Continue exploring"
  // follow-up click (which has no filter UI of its own) re-asks with the same
  // superseded/authority/provider context instead of silently resetting to defaults.
  const [lastFilters, setLastFilters] = useState<{
    supersededFilter: boolean;
    provider: AskRequest["provider"];
    model: string | undefined;
    authorityFilter: string | null;
  }>({ supersededFilter: true, provider: "openai", model: undefined, authorityFilter: null });

  const mutation = useMutation<AskResponse, AskError, AskRequest>({
    mutationFn: (req: AskRequest) =>
      streamAsk(req, (step) => {
        if (step.step === "answer_delta") {
          setStreamingText((prev) => prev + (step.detail ?? ""));
        } else if (step.step === "answer_reset") {
          // A provider fallback landed after some content already streamed (a
          // mid-stream OpenAI drop, not just an upfront failure) -- discard it so the
          // replacement answer doesn't run together with the abandoned partial one.
          setStreamingText("");
        } else {
          setSteps((prev) => [...prev, step]);
        }
      }),
    onSuccess: (response) => {
      setMessages((prev) => {
        const next: Message[] = [
          ...prev,
          {
            id: `${Date.now()}-a`,
            role: "assistant",
            content: !response.abstained ? response.answer : response.reason,
            timestamp: Date.now(),
            response,
          },
        ];
        return next.slice(-MAX_HISTORY);
      });
      setSteps([]);
      setStreamingText("");
    },
    onSettled: () => {
      askInFlightRef.current = false;
    },
  });

  const ask = (
    question: string,
    supersededFilter: boolean,
    provider: AskRequest["provider"],
    model: string | undefined,
    authorityFilter: string | null
  ) => {
    if (askInFlightRef.current) {
      setShowWaitWarning(true);
      setTimeout(() => setShowWaitWarning(false), 2500);
      return;
    }
    askInFlightRef.current = true;
    setShowWaitWarning(false);
    const userMessage: Message = { id: `${Date.now()}-u`, role: "user", content: question, timestamp: Date.now() };
    const history = messages.slice(-MAX_HISTORY).map((m) => ({ role: m.role, content: m.content }));
    setMessages((prev) => [...prev, userMessage].slice(-MAX_HISTORY));
    setPendingQuestion(question);
    setLastFilters({ supersededFilter, provider, model, authorityFilter });
    mutation.mutate({
      question,
      superseded_filter: supersededFilter,
      provider,
      model,
      authority_filter: authorityFilter,
      history,
    });
  };

  const askFollowUp = (question: string) =>
    ask(question, lastFilters.supersededFilter, lastFilters.provider, lastFilters.model, lastFilters.authorityFilter);

  // Which model actually answered most recently -- the OpenAI->NaraRouter fallback
  // means "openai" provider doesn't always mean gpt-4o-mini answered. Only ever set
  // from a real answer (never an abstention, which carries no model_used).
  const lastModelUsed = messages
    .slice()
    .reverse()
    .find((m) => m.role === "assistant" && m.response && m.response.abstained === false)
    ?.response as Answered | undefined;

  return (
    <AppShell onNewChat={() => setMessages([])}>
      <ChatHeader />
      <MessageList>
        {messages.map((m, i) => {
          if (m.role === "user") return <UserMessage key={m.id} content={m.content} />;
          const precedingQuestion = messages[i - 1]?.content ?? "";
          return (
            <AssistantMessage
              key={m.id}
              message={m}
              streamingText=""
              steps={[]}
              isStreaming={false}
              question={precedingQuestion}
              onAskFollowUp={askFollowUp}
              askPending={mutation.isPending}
            />
          );
        })}
        {mutation.isPending && (
          <AssistantMessage
            streamingText={streamingText}
            steps={steps}
            isStreaming
            question={pendingQuestion}
            onAskFollowUp={askFollowUp}
            askPending={mutation.isPending}
          />
        )}
      </MessageList>
      {mutation.isError && (
        <p className="px-4 pb-2 text-[12px]" style={{ color: "var(--superseded-rust)" }}>
          Something went wrong -- please try again.
        </p>
      )}
      {showWaitWarning && (
        <p className="px-4 pb-2 text-[12px]" style={{ color: "var(--ink-dim)" }}>
          Wait for the current response to finish before asking another question.
        </p>
      )}
      <ChatInput
        onSubmit={ask}
        isPending={mutation.isPending}
        showPrompts={messages.length === 0}
        lastModelUsed={lastModelUsed?.model_used}
      />
    </AppShell>
  );
}

export default function App() {
  return <Chat />;
}
