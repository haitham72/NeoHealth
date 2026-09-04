import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import AppShell from "./components/AppShell";
import ChatHeader from "./components/ChatHeader";
import ChatInput from "./components/ChatInput";
import MessageList from "./components/MessageList";
import UserMessage from "./components/UserMessage";
import AssistantMessage from "./components/AssistantMessage";
import OnboardingHome, { ONBOARDING_HOME_STORAGE_KEY } from "./components/OnboardingHome";
import OnboardingWelcome, { ONBOARDING_STORAGE_KEY } from "./components/OnboardingWelcome";
import FeaturePopup, { hasSeenFeaturePopup, markFeaturePopupSeen } from "./components/FeaturePopup";
import { AskError, createChat, getChat, saveChatMessage, streamAsk, useChatList } from "./api/client";
import { getClientId } from "./api/clientId";
import useBackendReady from "./hooks/useBackendReady";
import type { Answered, AskRequest, AskResponse, Message, TraceStep } from "./types/api";

const MAX_HISTORY = 10;

interface QueuedAsk {
  question: string;
  request: AskRequest;
}

function shouldShowHome(): boolean {
  try {
    return sessionStorage.getItem(ONBOARDING_HOME_STORAGE_KEY) !== "complete";
  } catch {
    return true;
  }
}

function shouldShowOnboarding(): boolean {
  try {
    return sessionStorage.getItem(ONBOARDING_STORAGE_KEY) !== "complete";
  } catch {
    return true;
  }
}

function Chat() {
  const { ready, elapsed, failed } = useBackendReady();
  const clientIdRef = useRef<string>(getClientId());
  const [messages, setMessages] = useState<Message[]>([]);
  const messagesRef = useRef<Message[]>([]);
  const [queuedAsks, setQueuedAsks] = useState<QueuedAsk[]>([]);
  const [steps, setSteps] = useState<TraceStep[]>([]);
  const [streamingText, setStreamingText] = useState("");
  const [pendingQuestion, setPendingQuestion] = useState("");
  const [showWaitWarning, setShowWaitWarning] = useState(false);
  // Three layers, shown in order: the full-page Home (vertical SaaS page) first, then
  // the blocking modal (OnboardingWelcome) over the chat, then the chat itself -- each
  // gated by its own sessionStorage key so a same-session remount doesn't replay any
  // of them once dismissed.
  const [showHome, setShowHome] = useState(shouldShowHome);
  const [showOnboarding, setShowOnboarding] = useState(() => !shouldShowHome() && shouldShowOnboarding());
  const [showFeaturePopup, setShowFeaturePopup] = useState(false);

  const completeHome = () => {
    setShowHome(false);
    if (shouldShowOnboarding()) setShowOnboarding(true);
  };
  // The chat a message gets persisted into. A ref because persistence side effects
  // (fired from async callbacks) need the current value synchronously, not a stale
  // closure from the render that scheduled them; mirrored into state so the sidebar
  // can highlight the active entry.
  const chatIdRef = useRef<string | null>(null);
  const [currentChatId, setCurrentChatId] = useState<string | null>(null);
  const { data: chatListData, refetch: refetchChats } = useChatList(clientIdRef.current);
  const chats = chatListData ?? [];
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
      const assistantMessage: Message = {
        id: `${Date.now()}-a`,
        role: "assistant",
        content: !response.abstained ? response.answer : response.reason,
        timestamp: Date.now(),
        response,
      };
      messagesRef.current = [...messagesRef.current, assistantMessage].slice(-MAX_HISTORY);
      setMessages(messagesRef.current);
      setSteps([]);
      setStreamingText("");
      const chatId = chatIdRef.current;
      if (chatId) {
        void saveChatMessage(chatId, clientIdRef.current, "assistant", assistantMessage.content, response).then(
          () => refetchChats()
        );
      }
    },
    onError: () => {
      setSteps([]);
      setStreamingText("");
    },
    onSettled: () => {
      askInFlightRef.current = false;
    },
  });

  const startAsk = (queuedAsk: QueuedAsk) => {
    askInFlightRef.current = true;
    setPendingQuestion(queuedAsk.question);
    setSteps([]);
    setStreamingText("");
    mutation.mutate(queuedAsk.request);
  };

  // Shown once, the first time the user actually reaches the chat (not during the
  // Home page or the onboarding modal) -- persisted in localStorage so it never
  // resurfaces automatically after that, unlike the per-session sessionStorage above.
  useEffect(() => {
    if (showHome || showOnboarding) return;
    if (!hasSeenFeaturePopup()) setShowFeaturePopup(true);
  }, [showHome, showOnboarding]);

  useEffect(() => {
    if (!ready || failed || mutation.isPending || askInFlightRef.current || queuedAsks.length === 0) return;
    const [next, ...remaining] = queuedAsks;
    setQueuedAsks(remaining);
    startAsk(next);
    // startAsk only changes request state; the queue and mutation state control this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [failed, mutation.isPending, queuedAsks, ready]);

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
    if (failed) {
      setShowWaitWarning(true);
      setTimeout(() => setShowWaitWarning(false), 2500);
      return;
    }
    setShowWaitWarning(false);
    const userMessage: Message = { id: `${Date.now()}-u`, role: "user", content: question, timestamp: Date.now() };
    const history = messagesRef.current.slice(-MAX_HISTORY).map((m) => ({ role: m.role, content: m.content }));
    messagesRef.current = [...messagesRef.current, userMessage].slice(-MAX_HISTORY);
    setMessages(messagesRef.current);
    setPendingQuestion(question);
    setLastFilters({ supersededFilter, provider, model, authorityFilter });
    const queuedAsk: QueuedAsk = {
      question,
      request: {
        question,
        superseded_filter: supersededFilter,
        provider,
        model,
        authority_filter: authorityFilter,
        history,
      },
    };
    if (ready) startAsk(queuedAsk);
    else setQueuedAsks((previous) => [...previous, queuedAsk]);

    // Best-effort persistence, off the critical path -- a chat is created lazily on
    // its first message (so idle visits that never ask anything don't clutter the
    // sidebar), and a failure here must never block or break the actual question.
    void (async () => {
      try {
        let chatId = chatIdRef.current;
        if (!chatId) {
          const created = await createChat(clientIdRef.current);
          chatId = created.id;
          chatIdRef.current = chatId;
          setCurrentChatId(chatId);
        }
        await saveChatMessage(chatId, clientIdRef.current, "user", question);
        refetchChats();
      } catch {
        // Chat history is a convenience layer -- never let it break asking a question.
      }
    })();
  };

  const askFollowUp = (question: string) =>
    ask(question, lastFilters.supersededFilter, lastFilters.provider, lastFilters.model, lastFilters.authorityFilter);

  const startNewChat = () => {
    chatIdRef.current = null;
    setCurrentChatId(null);
    messagesRef.current = [];
    setMessages([]);
    setQueuedAsks([]);
    setSteps([]);
    setStreamingText("");
  };

  const selectChat = (chatId: string) => {
    void (async () => {
      try {
        const detail = await getChat(chatId, clientIdRef.current);
        const loaded: Message[] = detail.messages.map((m, index) => ({
          id: `${detail.id}-${index}`,
          role: m.role,
          content: m.content,
          timestamp: Date.parse(m.created_at) || Date.now(),
          response: m.response ?? undefined,
        }));
        chatIdRef.current = detail.id;
        setCurrentChatId(detail.id);
        messagesRef.current = loaded.slice(-MAX_HISTORY);
        setMessages(messagesRef.current);
        setQueuedAsks([]);
        setSteps([]);
        setStreamingText("");
      } catch {
        // Leave the current conversation untouched if the chat failed to load.
      }
    })();
  };

  // Which model actually answered most recently -- the OpenAI->NaraRouter fallback
  // means "openai" provider doesn't always mean gpt-4o-mini answered. Only ever set
  // from a real answer (never an abstention, which carries no model_used).
  const lastModelUsed = messages
    .slice()
    .reverse()
    .find((m) => m.role === "assistant" && m.response && m.response.abstained === false)
    ?.response as Answered | undefined;

  const waitingForBackend = queuedAsks.length > 0 && !ready && !failed;
  const backendFailed = queuedAsks.length > 0 && failed;
  const showPendingAssistant = mutation.isPending || waitingForBackend || backendFailed;
  const pendingSteps = waitingForBackend
    ? [{ step: "waiting_for_backend", detail: String(elapsed) }]
    : backendFailed
      ? [{ step: "backend_unavailable" }]
      : steps;

  return (
    <>
      <AppShell
        onNewChat={startNewChat}
        onHome={() => setShowHome(true)}
        chats={chats}
        activeChatId={currentChatId}
        onSelectChat={selectChat}
      >
      <ChatHeader onOpenTips={() => setShowFeaturePopup(true)} />
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
        {showPendingAssistant && (
          <AssistantMessage
            streamingText={streamingText}
            steps={pendingSteps}
            isStreaming={mutation.isPending || waitingForBackend}
            question={pendingQuestion}
            onAskFollowUp={askFollowUp}
            askPending={mutation.isPending}
            errorText={backendFailed ? "The backend did not become ready within 120 seconds. Your question was not sent." : undefined}
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
          {failed ? "The server did not start. Please try again later." : "Wait for the current response to finish before asking another question."}
        </p>
      )}
      <ChatInput
        onSubmit={ask}
        isPending={mutation.isPending}
        showPrompts={messages.length === 0}
        lastModelUsed={lastModelUsed?.model_used}
      />
      </AppShell>
      {showHome && <OnboardingHome onComplete={completeHome} />}
      {!showHome && showOnboarding && <OnboardingWelcome onComplete={() => setShowOnboarding(false)} />}
      {showFeaturePopup && (
        <FeaturePopup
          onClose={() => {
            markFeaturePopupSeen();
            setShowFeaturePopup(false);
          }}
        />
      )}
    </>
  );
}

export default function App() {
  return <Chat />;
}
