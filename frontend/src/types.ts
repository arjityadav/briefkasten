// Mirrors the backend's pydantic models (RunState.public_view and /api/meta).

export type Urgency = "overdue" | "urgent" | "soon" | "relaxed" | "none";

export interface Triage {
  sender_type: string;
  sender_name: string | null;
  doc_type: string;
  is_official_decision: boolean;
  needs_response: boolean;
  confidence: number;
  reasoning: string;
}

export interface GlossaryTerm {
  german: string;
  english: string;
  note?: string | null;
}

export interface Explanation {
  summary_en: string;
  key_points: string[];
  if_ignored: string | null;
  glossary: GlossaryTerm[];
}

export interface DeadlineRule {
  kind: string;
  fixed_date?: string | null;
  reference_date?: string | null;
  months: number;
  weeks: number;
  days: number;
  delivery: string;
}

export interface ExtractedDeadline {
  id: string;
  purpose: string;
  kind: string;
  rule: DeadlineRule;
  quote: string;
}

export interface Amount {
  value: number;
  currency: string;
  purpose: string;
  direction: "you_pay" | "you_receive" | "info";
  quote: string;
}

export interface Reference {
  label: string;
  value: string;
  quote: string;
}

export interface Action {
  description: string;
  priority: "high" | "medium" | "low";
  quote: string | null;
}

export interface Extraction {
  letter_date: string | null;
  letter_date_quote: string | null;
  deadlines: ExtractedDeadline[];
  amounts: Amount[];
  references: Reference[];
  actions: Action[];
  has_legal_remedy_notice: boolean;
}

export interface ResolvedDeadline {
  id: string;
  purpose: string;
  kind: string;
  due_date: string | null;
  days_left: number | null;
  status: "overdue" | "due_today" | "open" | "unknown";
  basis: "stated" | "computed" | "estimated" | "unknown";
  steps: string[];
  quote: string;
}

export interface Issue {
  severity: "error" | "warning";
  source: string;
  field: string;
  message: string;
}

export interface Verification {
  passed: boolean;
  issues: Issue[];
  quotes_checked: number;
  extraction_rounds: number;
  repaired: string[];
}

export interface Draft {
  intent: string;
  subject_de: string;
  body_de: string;
  body_en: string;
  notes_en: string;
  alternatives: string[];
  status: "pending_approval" | "approved" | "rejected";
  edited_by_user: boolean;
  rejection_reason: string | null;
}

export interface PayslipLine {
  label: string;
  amount: number;
  category: "tax" | "social_insurance" | "other_deduction" | "addition";
  explanation_en: string;
  quote: string;
}

export interface PayslipAnalysis {
  family: "payslip";
  period: string;
  employer: string | null;
  tax_class: string | null;
  gross: number;
  gross_quote: string;
  net: number;
  net_quote: string;
  payout: number | null;
  payout_quote: string | null;
  lines: PayslipLine[];
  worth_checking_en: string[];
}

export interface Clause {
  title: string;
  explanation_en: string;
  quote: string;
}

export interface ContractAnalysis {
  family: "contract";
  contract_type: string;
  parties: string[];
  start_date: string | null;
  minimum_term: string | null;
  end_date: string | null;
  renewal: string | null;
  notice_period: string | null;
  recurring_costs: string[];
  key_terms: Clause[];
  watch_out: Clause[];
}

export interface FormField {
  label: string;
  explanation_en: string;
  what_to_enter: string;
  required: boolean | null;
}

export interface FormGuide {
  family: "form";
  form_name: string;
  purpose_en: string;
  fields: FormField[];
  attachments: string[];
  where_to_send: string | null;
  watch_out: string[];
}

export type Analysis = PayslipAnalysis | ContractAnalysis | FormGuide;

export interface TraceEvent {
  agent: string;
  model: string;
  attempt: number;
  duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  tool_calls: string[];
  thought: string | null;
  note: string | null;
}

export interface RunView {
  run_id: string;
  created_at: string;
  source_name: string;
  bundesland: string;
  today: string;
  received_on: string | null;
  reply_intent: string | null;
  image_reader: string | null;
  status: "running" | "awaiting_approval" | "done" | "failed";
  error: string | null;
  text_redacted: string;
  triage: Triage | null;
  explanation: Explanation | null;
  extraction: Extraction | null;
  deadlines: ResolvedDeadline[];
  verification: Verification | null;
  urgency: Urgency;
  draft: Draft | null;
  analysis: Analysis | null;
  supervisor_notes: string | null;
  trace: TraceEvent[];
  redaction_count: number;
}

export interface DemoInfo {
  title: string;
  kind: string;
  blurb: string;
}

export interface Meta {
  intents: Record<string, string>;
  states: Record<string, string>;
  default_state: string;
  provider: string;
  models: Record<string, string>;
  demos: string[];
  demo_only: boolean;
  imprint_url: string;
  privacy_url: string;
  image_reader: string;
  demo_descriptions: Record<string, DemoInfo>;
}

export interface RunSummary {
  run_id: string;
  created_at: string;
  status: RunView["status"];
  urgency: Urgency;
  sender: string | null;
  next_deadline: string | null;
}

export type JobEvent =
  | { type: "started"; agent: string; step: number; t: number }
  | { type: "decided"; agent: string; step: number; tools: string[]; thought: string | null; duration_ms: number; tokens: number; t: number }
  | { type: "problem"; agent: string; step: number; note: string | null; t: number }
  | { type: "info"; text: string; t: number }
  | { type: "done"; run: RunView; t: number }
  | { type: "error"; detail: string; t: number };
