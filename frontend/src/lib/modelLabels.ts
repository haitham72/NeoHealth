/** Backend model id -> display label for the "openai" provider slot. Shared between
 * ChatInput's provider button (reflects whichever model most recently answered) and
 * AssistantMessage's per-message fallback note. */
export const OPENAI_PROVIDER_MODEL_LABELS: Record<string, string> = {
  "gpt-4o-mini": "ChatGPT",
  "laguna-s-2.1": "Lagun",
};

export const OPENAI_PRIMARY_MODEL = "gpt-4o-mini";
export const OPENAI_FALLBACK_MODEL = "laguna-s-2.1";
