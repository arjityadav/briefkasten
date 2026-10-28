import { ALL_AGENTS, INTAKE, SPECIALISTS, SUPERVISOR, type AgentInfo, type LiveState, type NodeState } from "../agents";

const W = 780;
const H = 470;
const CX = 450;
const CY = 235;
const R = 30;

interface Props {
  live: LiveState;
  selected: string | null;
  onSelect: (agent: string | null) => void;
}

const idle: NodeState = { status: "idle", steps: 0, tokens: 0, problems: 0 };

export function AgentGraph({ live, selected, onSelect }: Props) {
  const ring = SPECIALISTS.map((a, i) => {
    const angle = -Math.PI / 2 + (i * 2 * Math.PI) / SPECIALISTS.length;
    return { agent: a, x: CX + 285 * Math.cos(angle), y: CY + 180 * Math.sin(angle) };
  });
  const usedIntake = INTAKE.filter((a) => a.id === "redactor" || live.nodes[a.id]);
  const intake = usedIntake.map((a, i) => ({ agent: a, x: 70, y: CY + (i - (usedIntake.length - 1) / 2) * 110 }));
  const redactor = intake.find((n) => n.agent.id === "redactor")!;

  return (
    <svg className="graph" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Agents at work">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="currentColor" />
        </marker>
      </defs>

      {/* intake: OCR/vision -> redactor -> supervisor */}
      {intake
        .filter((n) => n.agent.id !== "redactor")
        .map((n) => (
          <Edge key={n.agent.id} x1={n.x} y1={n.y} x2={redactor.x} y2={redactor.y} state={nodeOf(live, n.agent.id)} active={false} color={n.agent.color} />
        ))}
      <Edge x1={redactor.x} y1={redactor.y} x2={CX} y2={CY} state={nodeOf(live, "redactor")} active={false} color={SUPERVISOR.color} />

      {ring.map(({ agent, x, y }) => (
        <Edge key={agent.id} x1={CX} y1={CY} x2={x} y2={y} state={nodeOf(live, agent.id)} active={live.active.includes(agent.id)} color={agent.color} />
      ))}

      {[...intake, ...ring].map(({ agent, x, y }) => (
        // labels sit on the side away from the supervisor, so arrows never run through them
        <Node key={agent.id} agent={agent} x={x} y={y} state={nodeOf(live, agent.id)} selected={selected === agent.id} onSelect={onSelect} labelAbove={y < CY - 40} />
      ))}
      <Node agent={SUPERVISOR} x={CX} y={CY} state={nodeOf(live, "supervisor")} selected={selected === "supervisor"} onSelect={onSelect} big />
    </svg>
  );
}

const nodeOf = (live: LiveState, id: string) => live.nodes[id] ?? idle;

function Edge(props: { x1: number; y1: number; x2: number; y2: number; state: NodeState; active: boolean; color: string }) {
  const { x1, y1, x2, y2, state, active, color } = props;
  // shorten so the line starts and ends at the node rims
  const len = Math.hypot(x2 - x1, y2 - y1);
  const ux = (x2 - x1) / len;
  const uy = (y2 - y1) / len;
  const used = state.steps > 0;
  return (
    <line
      className={`edge ${active ? "active" : ""} ${used ? "used" : ""}`}
      x1={x1 + ux * (R + 12)}
      y1={y1 + uy * (R + 12)}
      x2={x2 - ux * (R + 6)}
      y2={y2 - uy * (R + 6)}
      style={{ color, stroke: active || used ? color : undefined }}
      markerEnd={active || used ? "url(#arrow)" : undefined}
    />
  );
}

function Node(props: {
  agent: AgentInfo;
  x: number;
  y: number;
  state: NodeState;
  selected: boolean;
  onSelect: (a: string | null) => void;
  big?: boolean;
  labelAbove?: boolean;
}) {
  const { agent, x, y, state, selected, onSelect, big, labelAbove } = props;
  const r = big ? R + 12 : R;
  const initials = agent.label.slice(0, 2);
  const cls = `node ${state.status} ${selected ? "selected" : ""} ${state.steps === 0 && state.status === "idle" ? "unused" : ""}`;
  const toggle = () => onSelect(selected ? null : agent.id);
  return (
    <g
      className={cls}
      transform={`translate(${x},${y})`}
      onClick={toggle}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && toggle()}
      tabIndex={0}
      role="button"
      aria-label={`${agent.label}: ${state.status}, ${state.steps} steps. Click to filter the log.`}
      style={{ color: agent.color }}
    >
      <title>{`${agent.label} (${agent.kind === "ai" ? "AI agent" : "plain code"})\n${agent.role}\n${state.steps} step(s) · ${state.tokens.toLocaleString()} tokens`}</title>
      {state.status === "thinking" && <circle className="pulse" r={r} />}
      {agent.kind === "ai" ? <circle className="body" r={r} /> : <rect className="body" x={-r} y={-r} width={r * 2} height={r * 2} rx={10} />}
      <text className="initials" dy="0.35em" fontSize={big ? 17 : 14}>
        {initials}
      </text>
      {state.steps > 0 && (
        <g transform={`translate(${r * 0.75},${labelAbove ? r * 0.75 : -r * 0.75})`}>
          <circle className="badge" r={10} />
          <text className="badge-text" dy="0.35em">
            {state.steps}
          </text>
        </g>
      )}
      {state.problems > 0 && (
        <g transform={`translate(${-r * 0.75},${labelAbove ? r * 0.75 : -r * 0.75})`}>
          <circle className="badge warn" r={10} />
          <text className="badge-text" dy="0.35em">
            !
          </text>
        </g>
      )}
      <text className="label" y={labelAbove ? -r - 24 : r + 17}>
        {agent.label}
      </text>
      {state.tokens > 0 && (
        <text className="sub" y={labelAbove ? -r - 10 : r + 31}>
          {state.tokens >= 1000 ? `${(state.tokens / 1000).toFixed(1)}k` : state.tokens} tok
        </text>
      )}
    </g>
  );
}

export const agentLabel = (id: string) => ALL_AGENTS[id]?.label ?? id;
