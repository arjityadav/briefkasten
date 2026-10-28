import { useState } from "react";
import { daysLabel, eur, fmtDate } from "../text";
import type { RunView } from "../types";

interface OverviewProps {
  run: RunView;
  onShowQuote: (q: string) => void;
  goTo: (tab: string) => void;
}

export function Overview({ run, onShowQuote, goTo }: OverviewProps) {
  const e = run.explanation;
  const next = run.deadlines.find((d) => d.due_date && (d.days_left ?? -1) >= 0);
  const pay = run.extraction?.amounts.filter((a) => a.direction === "you_pay") ?? [];
  return (
    <div className="overview">
      <div className="stats">
        <button className="stat" onClick={() => goTo("deadlines")} disabled={!next}>
          <span className="stat-num">{next ? Math.max(next.days_left ?? 0, 0) : "–"}</span>
          <span className="stat-label">{next ? `days until ${next.purpose.toLowerCase()}` : "no open deadline"}</span>
        </button>
        <button className="stat" onClick={() => pay[0] && onShowQuote(pay[0].quote)} disabled={!pay.length}>
          <span className="stat-num">{pay.length ? eur(pay[0].value) : "–"}</span>
          <span className="stat-label">{pay.length ? `to pay: ${pay[0].purpose}` : "nothing to pay"}</span>
        </button>
        <button className="stat" onClick={() => goTo("checks")}>
          <span className={`stat-num ${run.verification?.passed ? "ok-text" : "err-text"}`}>{run.verification ? (run.verification.passed ? "✓" : "✗") : "–"}</span>
          <span className="stat-label">{run.verification ? `${run.verification.quotes_checked} quotes checked against the document` : "not checked"}</span>
        </button>
        <button className="stat" onClick={() => goTo("agents")}>
          <span className="stat-num">{new Set(run.trace.map((x) => x.agent)).size}</span>
          <span className="stat-label">agents · {run.trace.length} steps</span>
        </button>
      </div>

      {e && (
        <section className="card">
          <h3>What this is</h3>
          <p className="summary">{e.summary_en}</p>
          <ul className="points">
            {e.key_points.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
          {e.if_ignored && (
            <div className="callout">
              <strong>If you do nothing:</strong> {e.if_ignored}
            </div>
          )}
        </section>
      )}

      {next && (
        <section className="card next-deadline" onClick={() => goTo("deadlines")} role="button" tabIndex={0}>
          <span className="muted small">Next deadline</span>
          <strong>{fmtDate(next.due_date)}</strong> — {next.purpose} <span className="muted">({daysLabel(next.days_left)})</span>
        </section>
      )}

      {run.extraction && run.extraction.actions.length > 0 && (
        <section className="card">
          <h3>To do</h3>
          <ul className="todo">
            {run.extraction.actions.map((a, i) => (
              <li key={i} className={`p-${a.priority}`}>
                <span>{a.description}</span>
                {a.quote && (
                  <button className="link" onClick={() => onShowQuote(a.quote!)}>
                    see in letter
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {e && e.glossary.length > 0 && (
        <section className="card">
          <h3>
            German words to know <span className="muted small">— click a card to flip it</span>
          </h3>
          <div className="glossary">
            {e.glossary.map((g, i) => (
              <FlipCard key={i} front={g.german} back={g.english} note={g.note} />
            ))}
          </div>
        </section>
      )}

      {run.supervisor_notes && (
        <section className="card supervisor-note">
          <span className="agent-chip static">Supervisor</span> {run.supervisor_notes}
        </section>
      )}
      <p className="muted small disclaimer">Produced by AI agents and checked by code — it can still be wrong. Not legal or tax advice.</p>
    </div>
  );
}

function FlipCard({ front, back, note }: { front: string; back: string; note?: string | null }) {
  const [flipped, setFlipped] = useState(false);
  return (
    <button className={`flip ${flipped ? "flipped" : ""}`} onClick={() => setFlipped(!flipped)} aria-label={`${front}: ${back}`}>
      <span className="flip-inner">
        <span className="flip-front">{front}</span>
        <span className="flip-back">
          {back}
          {note && <small>{note}</small>}
        </span>
      </span>
    </button>
  );
}

export function Checks({ run }: { run: RunView }) {
  const v = run.verification;
  if (!v) return <div className="card muted">No checks ran (the run stopped early).</div>;
  const errors = v.issues.filter((i) => i.severity === "error");
  const warnings = v.issues.filter((i) => i.severity === "warning");
  const total = v.quotes_checked;
  const pct = total ? Math.round(((total - errors.filter((e) => e.source === "grounding").length) / total) * 100) : 100;
  return (
    <div className="checks">
      <div className="card check-head">
        <svg viewBox="0 0 120 120" className="ring">
          <circle cx="60" cy="60" r="50" className="ring-bg" />
          <circle cx="60" cy="60" r="50" className={`ring-fg ${v.passed ? "ok" : "bad"}`} strokeDasharray={`${(pct / 100) * 314} 314`} transform="rotate(-90 60 60)" />
          <text x="60" y="58" textAnchor="middle" className="ring-num">
            {v.passed ? "✓" : "✗"}
          </text>
          <text x="60" y="80" textAnchor="middle" className="ring-sub">
            {total} quotes
          </text>
        </svg>
        <div>
          <h3>{v.passed ? "Every fact was found in the document" : "Some facts could not be verified"}</h3>
          <p className="muted">
            Code checked {total} quotes, every amount and every date against the document{v.extraction_rounds ? ` over ${v.extraction_rounds} extraction round(s)` : ""}.
            {v.repaired.length > 0 && ` The agents fixed ${v.repaired.length} problem(s) after being sent back.`}
          </p>
        </div>
      </div>
      {errors.length > 0 && <IssueList title="Errors (shown, not hidden)" items={errors.map((i) => i.message)} cls="err" />}
      {warnings.length > 0 && <IssueList title="Warnings" items={warnings.map((i) => i.message)} cls="warn" />}
      {v.repaired.length > 0 && <IssueList title="Caught and fixed by the agents themselves" items={v.repaired} cls="fixed" />}
    </div>
  );
}

function IssueList({ title, items, cls }: { title: string; items: string[]; cls: string }) {
  return (
    <section className={`card issues ${cls}`}>
      <h3>{title}</h3>
      <ul>
        {items.map((m, i) => (
          <li key={i}>{m}</li>
        ))}
      </ul>
    </section>
  );
}
