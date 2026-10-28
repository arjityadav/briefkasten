import type { JobEvent, RunView } from "./types";

export type AgentKind = "ai" | "code";

export interface AgentInfo {
  id: string;
  label: string;
  kind: AgentKind;
  role: string;
  color: string;
}

// The team, in the order they sit around the supervisor in the graph.
export const SPECIALISTS: AgentInfo[] = [
  { id: "triage", label: "Triage", kind: "ai", role: "Who sent it, what it is, reply needed?", color: "#2563eb" },
  { id: "explainer", label: "Explainer", kind: "ai", role: "Plain-English explanation", color: "#0891b2" },
  { id: "extractor", label: "Extractor", kind: "ai", role: "Deadlines as rules, amounts, references — each quoted", color: "#16a34a" },
  { id: "deadlines", label: "Calculator", kind: "code", role: "German deadline law: BGB, AO, ZPO, holidays", color: "#64748b" },
  { id: "critic", label: "Critic", kind: "ai", role: "Checks the explanation against the letter", color: "#ca8a04" },
  { id: "drafter", label: "Drafter", kind: "ai", role: "Formal German reply for your approval", color: "#4f46e5" },
  { id: "payslip", label: "Payslip", kind: "ai", role: "Every pay line; code checks the arithmetic", color: "#db2777" },
  { id: "contract", label: "Contract", kind: "ai", role: "Terms, renewal, clauses to watch out for", color: "#9333ea" },
  { id: "form", label: "Form", kind: "ai", role: "Each field explained", color: "#ea580c" },
];

export const INTAKE: AgentInfo[] = [
  { id: "ocr", label: "OCR", kind: "code", role: "Reads scans on this machine", color: "#64748b" },
  { id: "transcriber", label: "Vision", kind: "ai", role: "Reads scans with a vision model", color: "#64748b" },
  { id: "redactor", label: "Redactor", kind: "code", role: "Masks personal data before any AI call", color: "#64748b" },
];

export const SUPERVISOR: AgentInfo = {
  id: "supervisor",
  label: "Supervisor",
  kind: "ai",
  role: "Plans, delegates, judges, decides when it's done",
  color: "#b45309",
};

export const ALL_AGENTS: Record<string, AgentInfo> = Object.fromEntries(
  [SUPERVISOR, ...SPECIALISTS, ...INTAKE].map((a) => [a.id, a]),
);

/** Supervisor tool -> the specialist it delegates to. */
export const TOOL_AGENT: Record<string, string> = {
  triage_letter: "triage",
  explain_letter: "explainer",
  extract_facts: "extractor",
  compute_deadlines: "deadlines",
  review_explanation: "critic",
  draft_reply: "drafter",
  analyse_payslip: "payslip",
  analyse_contract: "contract",
  guide_form: "form",
};

export type NodeStatus = "idle" | "thinking" | "acting" | "done" | "problem";

export interface NodeState {
  status: NodeStatus;
  steps: number;
  tokens: number;
  problems: number;
  thought?: string | null;
}

export interface LiveState {
  events: JobEvent[];
  nodes: Record<string, NodeState>;
  active: string[]; // specialists currently working on a supervisor request (edges light up)
  run?: RunView;
  error?: string;
  finished: boolean;
}

export const emptyLive = (): LiveState => ({ events: [], nodes: {}, active: [], finished: false });

const node = (s: LiveState, id: string): NodeState => s.nodes[id] ?? { status: "idle", steps: 0, tokens: 0, problems: 0 };

export function reduceLive(prev: LiveState, e: JobEvent): LiveState {
  const s: LiveState = { ...prev, events: [...prev.events, e], nodes: { ...prev.nodes }, active: [...prev.active] };
  const set = (id: string, patch: Partial<NodeState>) => (s.nodes[id] = { ...node(s, id), ...patch });

  switch (e.type) {
    case "started":
      set(e.agent, { status: "thinking" });
      break;
    case "decided": {
      const n = node(s, e.agent);
      const submitted = e.tools.includes("submit");
      const oneShot = e.agent === "ocr" || e.agent === "transcriber";
      set(e.agent, {
        status: submitted || oneShot ? "done" : "acting",
        steps: n.steps + 1,
        tokens: n.tokens + e.tokens,
        thought: e.thought ?? n.thought,
      });
      if (e.agent === "supervisor") {
        for (const tool of e.tools) {
          const target = TOOL_AGENT[tool];
          if (!target) continue;
          if (ALL_AGENTS[target].kind === "code") {
            const c = node(s, target);
            set(target, { status: "done", steps: c.steps + 1 });
          } else if (!s.active.includes(target)) {
            s.active.push(target);
          }
        }
      } else if (submitted) {
        s.active = s.active.filter((a) => a !== e.agent);
      }
      break;
    }
    case "problem": {
      const n = node(s, e.agent);
      set(e.agent, { status: "problem", problems: n.problems + 1 });
      break;
    }
    case "info":
      if (e.text.startsWith("Redactor")) set("redactor", { status: "done", steps: 1 });
      break;
    case "done":
      s.run = e.run;
      s.finished = true;
      s.active = [];
      set("supervisor", { status: "done" });
      break;
    case "error":
      s.error = e.detail;
      s.finished = true;
      s.active = [];
      break;
  }
  return s;
}

/** Rebuild the live view from a finished run's trace (for runs opened from history). */
export function liveFromRun(run: RunView): LiveState {
  let s = emptyLive();
  let t = 0;
  s = reduceLive(s, { type: "info", text: `Redactor masked ${run.redaction_count} personal identifier(s) before any model call`, t });
  for (const ev of run.trace) {
    t += ev.duration_ms / 1000;
    s = reduceLive(s, { type: "started", agent: ev.agent, step: ev.attempt, t });
    s = reduceLive(s, {
      type: "decided",
      agent: ev.agent,
      step: ev.attempt,
      tools: ev.tool_calls,
      thought: ev.thought,
      duration_ms: ev.duration_ms,
      tokens: ev.input_tokens + ev.output_tokens,
      t,
    });
    if (ev.note) s = reduceLive(s, { type: "problem", agent: ev.agent, step: ev.attempt, note: ev.note, t });
  }
  return reduceLive(s, { type: "done", run, t });
}
