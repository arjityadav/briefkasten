import { useEffect, useRef, useState } from "react";
import { ALL_AGENTS, TOOL_AGENT, type LiveState } from "../agents";
import type { JobEvent } from "../types";
import { agentLabel } from "./AgentGraph";

interface Props {
  live: LiveState;
  filter: string | null;
  onFilter: (agent: string | null) => void;
}

export function LiveLog({ live, filter, onFilter }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [stick, setStick] = useState(true); // follow new events unless the user scrolled up

  const rows = live.events.filter((e) => e.type !== "started" && (!filter || ("agent" in e && e.agent === filter)));
  const thinking = Object.entries(live.nodes)
    .filter(([id, n]) => n.status === "thinking" && (!filter || id === filter))
    .map(([id]) => id);

  useEffect(() => {
    if (stick && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [rows.length, thinking.length, stick]);

  return (
    <div className="log-wrap">
      <div className="log-head">
        <strong>Live log</strong>
        {filter ? (
          <button className="chip" onClick={() => onFilter(null)} title="Show all agents">
            {agentLabel(filter)} ✕
          </button>
        ) : (
          <span className="muted small">click an agent in the graph to filter</span>
        )}
      </div>
      <div
        className="log"
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget;
          setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
        }}
        aria-live="polite"
      >
        {rows.map((e, i) => (
          <Row key={i} e={e} onFilter={onFilter} />
        ))}
        {thinking.map((id) => (
          <div className="row thinking" key={`t-${id}`}>
            <AgentChip id={id} onFilter={onFilter} />
            <span className="dots" aria-label="thinking">
              <i />
              <i />
              <i />
            </span>
          </div>
        ))}
        {!rows.length && !thinking.length && <div className="muted small pad">Waiting for the first agent…</div>}
      </div>
    </div>
  );
}

function AgentChip({ id, onFilter }: { id: string; onFilter: (a: string | null) => void }) {
  const a = ALL_AGENTS[id];
  return (
    <button className="agent-chip" style={{ background: a?.color ?? "#64748b" }} onClick={() => onFilter(id)} title={a?.role}>
      {agentLabel(id)}
    </button>
  );
}

function Row({ e, onFilter }: { e: JobEvent; onFilter: (a: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const time = <span className="t">{e.t.toFixed(1)}s</span>;
  switch (e.type) {
    case "decided": {
      const long = (e.thought?.length ?? 0) > 160;
      return (
        <div className={`row ${e.agent === "supervisor" ? "sup" : ""}`}>
          {time}
          <AgentChip id={e.agent} onFilter={onFilter} />
          <div className="body">
            <div className="tools">
              {e.tools.length ? (
                e.tools.map((tool, i) => (
                  <span key={i} className={`tool ${tool === "submit" ? "submit" : ""}`}>
                    {e.agent === "supervisor" && TOOL_AGENT[tool] ? `→ ${agentLabel(TOOL_AGENT[tool])}` : tool === "submit" ? "✓ submit" : tool}
                  </span>
                ))
              ) : (
                <span className="tool none">no action</span>
              )}
              <span className="muted small">
                {(e.duration_ms / 1000).toFixed(1)}s · {e.tokens.toLocaleString()} tok
              </span>
            </div>
            {e.thought && (
              <p className={`thought ${long && !open ? "clamp" : ""}`} onClick={() => long && setOpen(!open)} title={long ? "click to expand" : undefined}>
                “{e.thought}”
              </p>
            )}
          </div>
        </div>
      );
    }
    case "problem":
      return (
        <div className="row problem">
          {time}
          <AgentChip id={e.agent} onFilter={onFilter} />
          <div className="body">
            <strong>Rejected → fixing:</strong> {e.note}
          </div>
        </div>
      );
    case "info":
      return (
        <div className="row info">
          {time}
          <span className="body">{e.text}</span>
        </div>
      );
    case "done":
      return (
        <div className="row ok">
          {time}
          <span className="body">
            <strong>Finished</strong> · {e.run.trace.length} agent steps · {e.run.trace.reduce((n, x) => n + x.input_tokens + x.output_tokens, 0).toLocaleString()} tokens
          </span>
        </div>
      );
    case "error":
      return (
        <div className="row err">
          {time}
          <span className="body">
            <strong>Failed:</strong> {e.detail}
          </span>
        </div>
      );
    default:
      return null;
  }
}
