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
    <div className="border-t px-6 py-4" style={{ borderColor: "var(--rule)", background: "var(--fhir-surface)", fontFamily: "var(--font-display)" }}>
      {showPrompts && (
        <div className="mb-3 flex flex-wrap gap-2">
          {EXAMPLE_QUESTIONS.map((q) => (
            <button
              key={q}
              type="button"
              disabled={isPending}
              onClick={() => submit(q)}
              className="rounded-full px-3 py-1.5 text-[12px] disabled:opacity-50"
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
          className="flex-1 rounded-md px-4 py-3 text-[14px] outline-none"
          style={{ background: "var(--fhir-bg)", color: "var(--ink)", border: "1px solid var(--rule)" }}
        />
        <button
          type="submit"
          disabled={isPending || (provider === "local" && !model)}
          className="rounded-md px-5 py-3 text-[13px] font-semibold uppercase tracking-[0.04em] disabled:opacity-50"
          style={{ background: "var(--fhir-blue)", color: "#fff" }}
        >
          Send
        </button>
      </form>

      <div className="mt-2 flex flex-wrap items-center gap-3 text-[12px]" style={{ color: "var(--ink-dim)" }}>
        <div className="inline-flex rounded-md p-0.5" style={{ background: "var(--fhir-bg)", border: "1px solid var(--rule)" }}>
          {(["openai", "local"] as const).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setProvider(p)}
              className="rounded-[5px] px-3 py-1"
              style={{ background: provider === p ? "var(--fhir-blue)" : "transparent", color: provider === p ? "#fff" : "var(--ink-dim)" }}
              title={p === "local" ? "Run the app locally with LM Studio to see your local models.\nKeeps inference fully on your machine — for privacy." : undefined}
            >
              {p === "openai" ? openaiProviderLabel : "Local"}
            </button>
          ))}
        </div>

        {provider === "local" &&
          (models.length > 0 ? (
            <select value={model ?? ""} onChange={(e) => setModel(e.target.value)} className="rounded-md px-2 py-1.5 outline-none" style={{ border: "1px solid var(--rule)" }}>
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          ) : (
            <span
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
          className="rounded-md px-2 py-1.5 outline-none"
          style={{ border: "1px solid var(--rule)" }}
        >
          {AUTHORITIES.map((a) => (
            <option key={a.value} value={a.value}>{a.label}</option>
          ))}
        </select>

        <label className="flex items-center gap-1.5 cursor-pointer select-none">
          <input type="checkbox" checked={supersededFilter} onChange={(e) => setSupersededFilter(e.target.checked)} className="h-3.5 w-3.5" />
          Exclude outdated regulations
        </label>
      </div>
    </div>
  );
}
