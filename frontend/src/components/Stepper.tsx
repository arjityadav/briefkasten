import type { LiveState } from "../agents";

// The stages of a run, each done by one of these agents. Letters, payslips, contracts and forms
// take different paths, so stages that never ran are marked "not needed" once the run is over.
const STAGES: { label: string; agents: string[] }[] = [
  { label: "Personal data masked", agents: ["redactor"] },
  { label: "Document classified", agents: ["triage"] },
  { label: "Explained", agents: ["explainer"] },
  { label: "Facts extracted", agents: ["extractor", "payslip", "contract", "form"] },
  { label: "Deadlines computed", agents: ["deadlines"] },
  { label: "Checked against the document", agents: ["critic"] },
  { label: "Reply drafted", agents: ["drafter"] },
];

type StageStatus = "pending" | "current" | "done" | "skipped";

function status(live: LiveState, agents: string[]): StageStatus {
  const nodes = agents.map((a) => live.nodes[a]).filter(Boolean);
  if (agents.some((a) => live.active.includes(a)) || nodes.some((n) => n.status === "thinking" || n.status === "acting" || n.status === "problem")) {
    return live.finished ? "done" : "current";
  }
  if (nodes.some((n) => n.status === "done" || n.steps > 0)) return "done";
  return live.finished ? "skipped" : "pending";
}

export function Stepper({ live }: { live: LiveState }) {
  return (
    <ol className="stepper" aria-label="Progress">
      {STAGES.map((s) => {
        const st = status(live, s.agents);
        return (
          <li key={s.label} className={`stage ${st}`} aria-current={st === "current" ? "step" : undefined}>
            <span className="mark" aria-hidden="true">
              <svg viewBox="0 0 16 16">
                <path d="M3.5 8.5l3 3 6-7" />
              </svg>
            </span>
            <span className="stage-label">
              {s.label}
              {st === "skipped" && <span className="sr-only"> (not needed)</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
