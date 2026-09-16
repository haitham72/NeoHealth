import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { Message, Provider, RetrievedChunk, TraceStep } from "../types/api";
import ThinkingSteps from "./ThinkingSteps";
import SourceCard from "./SourceCard";
import CitationPopover from "./CitationPopover";
import ReportAnswer from "./ReportAnswer";
import FollowUpQuestions from "./FollowUpQuestions";
import CacheNotice from "./CacheNotice";
import { renderWithCitations } from "../lib/citations";
import { pickFollowUpQuestions } from "../lib/followUpQuestions";
import { OPENAI_FALLBACK_MODEL, OPENAI_PROVIDER_MODEL_LABELS } from "../lib/modelLabels";

interface Props {
  message?: Message;
  streamingText: string;
  steps: TraceStep[];
  isStreaming: boolean;
  question: string;
  onAskFollowUp: (question: string) => void;
  errorText?: string;
  provider: Provider;
  model?: string;
  /** True while any /ask is in flight (there's only ever one at a time) -- disables
   * follow-up buttons on already-completed messages so repeated clicks can't queue up
   * multiple concurrent requests. */
  askPending: boolean;
}

function textToNodes(text: string, chunks: RetrievedChunk[], onOpen: (i: number) => void) {
  return renderWithCitations(text, chunks, onOpen);
}

export default function AssistantMessage({ message, streamingText, steps, isStreaming, question, onAskFollowUp, askPending, errorText, provider, model }: Props) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const response = message?.response;
  const chunks: RetrievedChunk[] = response && !response.abstained ? (response.retrieved_chunks ?? []).filter((c) => c.used_for_answer) : [];

  // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on message.id so the
  // picked set stays stable across re-renders of the same message (Math.random()
  // tiebreak would otherwise reshuffle on every parent update, e.g. while later
  // messages stream).
  const followUps = useMemo(() => {
    if (!response || response.abstained) return [];
    // Mined suggestions are grounded in real documents (matched server-side
    // against this question), so they win over the static keyword bank, which
    // stays as the fallback for answers with no mined match.
    if (response.suggested_followups?.length) {
      return response.suggested_followups.map((s) => s.question);
    }
    return pickFollowUpQuestions({
      question,
      answer: response.answer,
      authority: chunks[0]?.document?.authority ?? null,
      exclude: [question],
    });
  }, [message?.id]);

  return (
    <div className="flex justify-start">
      <div
        className="max-w-[85%] rounded-2xl rounded-tl-sm px-4 py-3 text-[14px] leading-relaxed sm:max-w-[75%]"
        style={{ background: "var(--fhir-surface)", border: "1px solid var(--rule)", color: "var(--ink)", fontFamily: "var(--font-body)" }}
      >
        <ThinkingSteps steps={steps} active={isStreaming} />

        {isStreaming && !message && <div className="whitespace-pre-wrap">{streamingText}</div>}

        {!message && errorText && <div className="text-[13px]" style={{ color: "var(--superseded-rust)" }}>{errorText}</div>}

        {message && response && response.abstained && (
          <>
            {response.smalltalk || response.blocked ? (
              // Conversational replies (backend small-talk router) and safety-screen
              // refusals: `reason` is already the complete, fixed response text, so
              // it's rendered as prose with no abstention framing around it. Small
              // talk reads as a normal reply, a refusal stays dimmed like one.
              <>
                <p style={{ color: response.smalltalk ? "var(--ink)" : "var(--ink-dim)" }}>{response.reason}</p>
                {response.suggested_followups?.length ? (
                  <FollowUpQuestions
                    questions={response.suggested_followups.map((s) => s.question)}
                    onAsk={onAskFollowUp}
                    disabled={askPending}
                  />
                ) : null}
              </>
            ) : response.off_topic ? (
              <>
                <p style={{ color: "var(--ink-dim)" }}>{response.reason}</p>
                {response.suggested_followups?.length ? (
                  <FollowUpQuestions
                    questions={response.suggested_followups.map((s) => s.question)}
                    onAsk={onAskFollowUp}
                    disabled={askPending}
                  />
                ) : null}
              </>
            ) : (
              <p style={{ color: "var(--ink-dim)" }}>I don't have current guidance on that. ({response.reason})</p>
            )}
            {/* No ReportAnswer on a conversational reply: a "report this answer"
                control under "Hello!" is noise, and there is no retrieval behind it
                to report on. */}
            {response.run_id && !response.smalltalk && !response.blocked && (
              <ReportAnswer runId={response.run_id} variant="abstained" />
            )}
          </>
        )}

        {message && response && !response.abstained && (
          <>
            {response.model_used === OPENAI_FALLBACK_MODEL && !response.cache_hit && (
              <div
                className="mb-2 inline-flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-[11px] font-semibold tracking-[0.02em]"
                style={{ color: "var(--ink-dim)", background: "var(--paper)", border: "1px dashed var(--rule)" }}
              >
                <span aria-hidden>&#8644;</span>
                Switched to {OPENAI_PROVIDER_MODEL_LABELS[OPENAI_FALLBACK_MODEL]} -- ChatGPT was temporarily
                unavailable
              </div>
            )}
            <ReactMarkdown
              components={{
                p: ({ children }) => (
                  <p className="mb-2 last:mb-0">
                    {typeof children === "string" ? textToNodes(children, chunks, setOpenIndex) : children}
                  </p>
                ),
                li: ({ children }) => (
                  <li>{typeof children === "string" ? textToNodes(children, chunks, setOpenIndex) : children}</li>
                ),
                strong: ({ children }) => <strong className="font-bold">{children}</strong>,
                ul: ({ children }) => <ul className="mb-2 ml-5 list-disc space-y-1 last:mb-0">{children}</ul>,
              }}
            >
              {response.answer}
            </ReactMarkdown>
            {response.cache_hit && <CacheNotice token={response.cache_token} className="mt-2 text-[11px]" />}
            <SourceCard chunks={chunks} onOpen={setOpenIndex} />
            <FollowUpQuestions questions={followUps} onAsk={onAskFollowUp} disabled={askPending} />
            {response.run_id && <ReportAnswer runId={response.run_id} variant="answered" />}
          </>
        )}
      </div>

      {openIndex !== null && chunks[openIndex] && (
        <CitationPopover chunk={chunks[openIndex]} index={openIndex} sources={chunks} question={question} provider={provider} model={model} onClose={() => setOpenIndex(null)} />
      )}
    </div>
  );
}
