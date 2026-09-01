export type DocumentTier = "official" | "research" | "commentary";

export interface HistoryTurn {
  role: "user" | "assistant";
  content: string;
}

export interface Document {
  id: number;
  title: string;
  doc_code: string;
  version: string;
  effective_date: string;
  authority: string;
  source_url: string | null;
  superseded: boolean;
  tier: DocumentTier;
}

export interface SiblingVersion {
  id: number;
  version: string;
  effective_date: string;
  superseded: boolean;
  is_current: boolean;
}

export interface Abstained {
  abstained: true;
  reason: string;
  top_score?: number;
  run_id?: string | null;
}

export interface BBox {
  page_no: number;
  l: number;
  t: number;
  r: number;
  b: number;
}

export interface RetrievedChunk {
  chunk_id: number;
  document_id: number;
  page: number;
  page_end: number;
  heading_path: string[];
  bboxes: BBox[];
  text: string;
  semantic_score: number;
  rrf: number;
  used_for_answer: boolean;
  document?: Document | null;
}

export type ConfidenceTier = "high" | "medium" | "low";

export interface Answered {
  abstained: false;
  answer: string;
  model_used?: string;
  top_score: number;
  confidence_tier: ConfidenceTier;
  document: Document;
  page: number;
  page_end: number;
  heading_path: string[];
  bboxes: BBox[];
  superseded_excluded: number;
  sibling_versions?: SiblingVersion[];
  retrieved_chunks?: RetrievedChunk[];
  run_id?: string | null;
}

export type AskResponse = Abstained | Answered;

export type Provider = "openai" | "local";

export interface AskRequest {
  question: string;
  superseded_filter: boolean;
  provider: Provider;
  model?: string;
  authority_filter?: string | null;
  history?: HistoryTurn[];
}

export interface DiffFollowupRequest {
  doc_code: string;
  current_document_id: number;
  cited_text: string;
  cited_page: number;
  question: string;
}

export interface DiffFollowupResult {
  available: true;
  previous_version: string;
  previous_effective_date: string;
  explanation: string;
}

export interface DiffFollowupUnavailable {
  available: false;
  reason: string;
}

export type DiffFollowupResponse = DiffFollowupResult | DiffFollowupUnavailable;

export interface CrossCheckRegulationRequest {
  doc_code: string;
  current_document_id: number;
  cited_text: string;
  cited_page: number;
  question: string;
}

export interface RelatedOfficialDocument {
  doc_code: string;
  title: string;
  version: string;
  authority: string;
}

export interface CrossCheckRegulationResult {
  available: true;
  explanation: string;
  documents: RelatedOfficialDocument[];
}

export interface CrossCheckRegulationUnavailable {
  available: false;
  reason: string;
}

export type CrossCheckRegulationResponse = CrossCheckRegulationResult | CrossCheckRegulationUnavailable;

export interface TraceStep {
  step: string;
  detail?: string;
  title?: string;
  doc_code?: string;
  version?: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  /** Only present on assistant messages that resolved to a real answer or
   * abstention -- absent while a message is still streaming. */
  response?: AskResponse;
}

export type ReportReason = "wrong_citation" | "unrelated" | "incorrect_abstention" | "other";

export interface ReportAnswerRequest {
  run_id: string;
  reason: ReportReason;
  comment?: string;
}

export interface ReportAnswerResponse {
  success: boolean;
  reason?: string;
}
