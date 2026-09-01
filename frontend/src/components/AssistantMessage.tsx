import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { Message, RetrievedChunk, TraceStep } from "../types/api";
import ThinkingSteps from "./ThinkingSteps";
import SourceCard from "./SourceCard";
import CitationPopover from "./CitationPopover";
import ReportAnswer from "./ReportAnswer";
import FollowUpQuestions from "./FollowUpQuestions";
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
  /** True while any /ask is in flight (there's only ever one at a time) -- disables
   * follow-up buttons on already-completed messages so repeated clicks can't queue up
   * multiple concurrent requests. */
  askPending: boolean;
}

function textToNodes(text: string, chunks: RetrievedChunk[], onOpen: (i: number) => void) {
  return renderWithCitations(text, chunks, onOpen);
}

export default function AssistantMessage({ message, streamingText, steps, isStreaming, question, onAskFollowUp, askPending }: Props) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const response = message?.response;
  const chunks: RetrievedChunk[] = response && !response.abstained ? (response.retrieved_chunks ?? []).filter((c) => c.used_for_answer) : [];

  // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on message.id so the
  // picked set stays stable across re-renders of the same message (Math.random()
  // tiebreak would otherwise reshuffle on every parent update, e.g. while later
  // messages stream).
  const followUps = useMemo(() => {
    if (!response || response.abstained) return [];
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
        className="max-w-[75%] rounded-2xl rounded-tl-sm px-4 py-3 text-[14px] leading-relaxed"
        style={{ background: "var(--fhir-surface)", border: "1px solid var(--rule)", color: "var(--ink)", fontFamily: "var(--font-body)" }}
      >
        <ThinkingSteps steps={steps} active={isStreaming} />

        {isStreaming && !message && <div className="whitespace-pre-wrap">{streamingText}</div>}

        {message && response && response.abstained && (
          <>
            <p style={{ color: "var(--ink-dim)" }}>I don't have current guidance on that. ({response.reason})</p>
            {response.run_id && <ReportAnswer runId={response.run_id} variant="abstained" />}
          </>
        )}

        {message && response && !response.abstained && (
          <>
            {response.model_used === OPENAI_FALLBACK_MODEL && (
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
            <SourceCard chunks={chunks} onOpen={setOpenIndex} />
            <FollowUpQuestions questions={followUps} onAsk={onAskFollowUp} disabled={askPending} />
            {response.run_id && <ReportAnswer runId={response.run_id} variant="answered" />}
          </>
        )}
      </div>

      {openIndex !== null && chunks[openIndex] && (
        <CitationPopover chunk={chunks[openIndex]} index={openIndex} sources={chunks} question={question} onClose={() => setOpenIndex(null)} />
      )}
    </div>
  );
}
