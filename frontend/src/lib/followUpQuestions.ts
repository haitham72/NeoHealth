import questionsMd from "../data/followUpQuestions.md?raw";

interface TaggedQuestion {
  tag: string | null;
  text: string;
}

const AUTHORITY_TAG: Record<string, string> = {
  "Dubai Health Authority": "DHA",
  "Department of Health - Abu Dhabi": "DoH",
  "Ministry of Health and Prevention": "MOHAP",
};

const STOPWORDS = new Set([
  "the", "a", "an", "is", "are", "was", "were", "of", "to", "in", "for", "and", "or",
  "what", "how", "does", "do", "on", "with", "by", "this", "that", "it", "as", "be",
  "can", "if", "not", "any", "at", "from", "when", "which",
]);

function parseQuestions(md: string): TaggedQuestion[] {
  const out: TaggedQuestion[] = [];
  for (const line of md.split("\n")) {
    const m = line.match(/^-\s*(?:\[(\w+)\]\s*)?(.+)$/);
    if (!m) continue;
    out.push({ tag: m[1] ?? null, text: m[2].trim() });
  }
  return out;
}

function keywords(text: string): Set<string> {
  return new Set(
    text
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, " ")
      .split(/\s+/)
      .filter((w) => w.length > 3 && !STOPWORDS.has(w))
  );
}

const ALL_QUESTIONS = parseQuestions(questionsMd);

/** Picks up to `count` follow-up questions for the "Continue exploring" panel under an
 * answer: favors questions tagged for the same authority the answer came from, then
 * keyword overlap with the question+answer text, with a small random tiebreak so the
 * same top-scoring set doesn't always render identically. Falls back gracefully to
 * whatever's in the bank -- including fewer than `count` results, or none -- rather
 * than erroring, since the bank is expected to start small and grow over time. */
export function pickFollowUpQuestions(params: {
  question: string;
  answer: string;
  authority?: string | null;
  exclude?: string[];
  count?: number;
}): string[] {
  const { question, answer, authority, exclude = [], count = 3 } = params;

  const excludeSet = new Set(exclude.map((q) => q.trim().toLowerCase()));
  const pool = ALL_QUESTIONS.filter((q) => !excludeSet.has(q.text.toLowerCase()));
  if (pool.length === 0) return [];

  const wantedTag = authority ? AUTHORITY_TAG[authority] : undefined;
  const contextWords = keywords(`${question} ${answer}`);

  const scored = pool.map((q) => {
    let score = 0;
    if (wantedTag && q.tag === wantedTag) score += 3;
    if (!q.tag) score += 0.5;
    for (const w of keywords(q.text)) if (contextWords.has(w)) score += 1;
    return { q, score: score + Math.random() * 0.1 };
  });

  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, count).map((s) => s.q.text);
}
