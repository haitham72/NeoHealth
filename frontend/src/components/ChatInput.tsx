import { useEffect, useState } from "react";
import { useLocalModels } from "../api/client";
import { OPENAI_PROVIDER_MODEL_LABELS } from "../lib/modelLabels";
import type { Provider } from "../types/api";

interface Props {
  onSubmit: (
    question: string,
    supersededFilter: boolean,
    provider: Provider,
    model: string | undefined,
    authorityFilter: string | null
  ) => void;
  isPending: boolean;
  showPrompts: boolean;
  /** The model that actually answered the most recent question. The "openai" provider
   * button falls back to NaraRouter under the hood (see retrieval.py's chat_completion())
   * when OpenAI is rate-limited or down, so its label reflects whichever model is
   * actually live right now rather than always claiming ChatGPT answered. */
  lastModelUsed?: string;
}

const EXAMPLE_QUESTIONS = [
  "What happens if a healthcare professional's license application is rejected?",
  "What are the requirements for licensing a healthcare professional in Dubai?",
  "What is the structure of Abu Dhabi health regulations?",
];

const AUTHORITIES = [
  { value: "", label: "All authorities" },
  { value: "Dubai Health Authority", label: "DHA (Dubai)" },
  { value: "Department of Health - Abu Dhabi", label: "DoH (Abu Dhabi)" },
  { value: "Ministry of Health and Prevention", label: "MOHAP (federal)" },
];

export default function ChatInput({ onSubmit, isPending, showPrompts, lastModelUsed }: Props) {
  const openaiProviderLabel = (lastModelUsed && OPENAI_PROVIDER_MODEL_LABELS[lastModelUsed]) || "ChatGPT";
  const [question, setQuestion] = useState("");
  const [supersededFilter, setSupersededFilter] = useState(true);
  const [authorityFilter, setAuthorityFilter] = useState("");
  const [provider, setProvider] = useState<Provider>("openai");
  const [model, setModel] = useState<string | undefined>(undefined);

  const localModels = useLocalModels();
  const models = localModels.data?.models ?? [];

  useEffect(() => {
    if (provider !== "local") return;
    if (model && models.includes(model)) return;
    setModel(models.find((m) => m.toLowerCase().includes("qwen")) ?? models[0]);
  }, [provider, models, model]);

  const submit = (q: string) => {
    const trimmed = q.trim();
    if (!trimmed || isPending) return;
    if (provider === "local" && !model) return;
    onSubmit(trimmed, supersededFilter, provider, provider === "local" ? model : undefined, authorityFilter || null);
    setQuestion("");
  };

  return (
    <div
      className="border-t px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-6 sm:py-4"
      style={{ borderColor: "var(--rule)", background: "var(--fhir-surface)", fontFamily: "var(--font-display)" }}
    >
      {/* Below sm these are a single horizontally-scrolling row rather than a wrapping
          one: three ~48-char pills wrapped in a phone-width column produced a stack of
          ragged part-lines. `-mx-4 px-4` lets the row bleed to the screen edge so the
          last chip is visibly cut off, which is what signals it scrolls. */}
      {showPrompts && (
        <div className="regulense-xscroll -mx-4 mb-3 flex snap-x snap-mandatory gap-2 overflow-x-auto px-4 sm:mx-0 sm:flex-wrap sm:overflow-visible sm:px-0">
          {EXAMPLE_QUESTIONS.map((q) => (
            <button
              key={q}
              type="button"
              disabled={isPending}
              onClick={() => submit(q)}
              className="shrink-0 snap-start truncate rounded-full px-3 py-2 text-[12px] disabled:opacity-50 max-w-[78vw] sm:max-w-none sm:py-1.5"
              style={{ border: "1px solid var(--rule)", color: "var(--ink-dim)" }}
            >
              {q.length > 48 ? q.slice(0, 48) + "…" : q}
            </button>
          ))}
        </div>
      )}

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit(question);
        }}
      >
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about regulations..."
          autoFocus
          // 16px on mobile is deliberate: iOS Safari auto-zooms the whole page when a
          // focused input is under 16px, and never zooms back out.
          className="min-w-0 flex-1 rounded-md px-4 py-3 text-[16px] outline-none sm:text-[14px]"
          style={{ background: "var(--fhir-bg)", color: "var(--ink)", border: "1px solid var(--rule)" }}
        />
        <button
          type="submit"
          disabled={isPending || (provider === "local" && !model)}
          aria-label="Send"
          className="grid shrink-0 place-items-center rounded-md px-4 py-3 text-[13px] font-semibold uppercase tracking-[0.04em] disabled:opacity-50 sm:px-5"
          style={{ background: "var(--fhir-blue)", color: "#fff" }}
        >
          <span className="hidden sm:inline">Send</span>
          <svg viewBox="0 0 16 16" className="h-4 w-4 sm:hidden" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <line x1="8" y1="13" x2="8" y2="3" />
            <polyline points="3.5,7.5 8,3 12.5,7.5" />
          </svg>
        </button>
      </form>

      {/* Same treatment as the prompt chips: one scrolling row on a phone, the original
          wrapping row from sm up. */}
      <div
        className="regulense-xscroll -mx-4 mt-2 flex items-center gap-3 overflow-x-auto px-4 pb-0.5 text-[12px] sm:mx-0 sm:flex-wrap sm:overflow-visible sm:px-0"
        style={{ color: "var(--ink-dim)" }}
      >
        <div className="inline-flex shrink-0 rounded-md p-0.5" style={{ background: "var(--fhir-bg)", border: "1px solid var(--rule)" }}>
          {(["openai", "local"] as const).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setProvider(p)}
              className="whitespace-nowrap rounded-[5px] px-3 py-1"
              style={{ background: provider === p ? "var(--fhir-blue)" : "transparent", color: provider === p ? "#fff" : "var(--ink-dim)" }}
              title={p === "local" ? "Run the app locally with LM Studio to see your local models.\nKeeps inference fully on your machine — for privacy." : undefined}
            >
              {p === "openai" ? openaiProviderLabel : "Local"}
            </button>
          ))}
        </div>

        {provider === "local" &&
          (models.length > 0 ? (
            <select value={model ?? ""} onChange={(e) => setModel(e.target.value)} className="max-w-[45vw] shrink-0 rounded-md px-2 py-1.5 outline-none sm:max-w-none" style={{ border: "1px solid var(--rule)" }}>
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          ) : (
            <span
              className="shrink-0 whitespace-nowrap"
              style={{ color: "var(--superseded-rust)" }}
              title={localModels.isLoading ? undefined : "Run the app locally with LM Studio to see your local models.\nKeeps inference fully on your machine — for privacy."}
            >
              {localModels.isLoading ? "Checking LM Studio…" : "No local models found"}
            </span>
          ))}

        <select
          aria-label="Filter by authority"
          value={authorityFilter}
          onChange={(e) => setAuthorityFilter(e.target.value)}
          className="shrink-0 rounded-md px-2 py-1.5 outline-none"
          style={{ border: "1px solid var(--rule)" }}
        >
          {AUTHORITIES.map((a) => (
            <option key={a.value} value={a.value}>{a.label}</option>
          ))}
        </select>

        <label className="flex shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap select-none">
          <input type="checkbox" checked={supersededFilter} onChange={(e) => setSupersededFilter(e.target.checked)} className="h-3.5 w-3.5" />
          Exclude outdated regulations
        </label>
      </div>
    </div>
  );
}
