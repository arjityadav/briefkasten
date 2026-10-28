import { useEffect, useState } from "react";
import { eur, fmtDate } from "../text";
import type { Analysis as AnalysisT, ContractAnalysis, FormGuide, PayslipAnalysis, PayslipLine, RunView } from "../types";

interface Props {
  run: RunView;
  analysis: AnalysisT;
  onShowQuote: (q: string) => void;
}

export function Analysis({ run, analysis, onShowQuote }: Props) {
  if (analysis.family === "payslip") return <Payslip a={analysis} onShowQuote={onShowQuote} />;
  if (analysis.family === "contract") return <Contract run={run} a={analysis} onShowQuote={onShowQuote} />;
  return <Form runId={run.run_id} a={analysis} />;
}

// --------------------------------------------------------------------------- payslip waterfall

interface Bar {
  key: string;
  label: string;
  from: number;
  to: number;
  total?: boolean;
  lines: PayslipLine[];
}

function Payslip({ a, onShowQuote }: { a: PayslipAnalysis; onShowQuote: (q: string) => void }) {
  const sum = (cat: PayslipLine["category"]) => a.lines.filter((l) => l.category === cat);
  const total = (ls: PayslipLine[]) => ls.reduce((n, l) => n + l.amount, 0);
  const taxes = sum("tax");
  const social = sum("social_insurance");
  const other = [...sum("other_deduction"), ...sum("addition")];
  const otherDelta = total(sum("addition")) - total(sum("other_deduction"));
  const bars: Bar[] = [
    { key: "gross", label: "Gross", from: 0, to: a.gross, total: true, lines: [] },
    { key: "tax", label: "Taxes", from: a.gross, to: a.gross - total(taxes), lines: taxes },
    { key: "social", label: "Social insurance", from: a.gross - total(taxes), to: a.net, lines: social },
    { key: "net", label: "Net", from: 0, to: a.net, total: true, lines: [] },
  ];
  if (a.payout !== null) {
    if (other.length) bars.push({ key: "other", label: otherDelta < 0 ? "Deductions after net" : "Additions", from: a.net, to: a.net + otherDelta, lines: other });
    bars.push({ key: "payout", label: "Paid out", from: 0, to: a.payout, total: true, lines: [] });
  }
  const [sel, setSel] = useState<string>("tax");
  const max = Math.max(...bars.map((b) => Math.max(b.from, b.to)));
  const W = 660;
  const H = 260;
  const bw = W / bars.length;
  const yOf = (v: number) => 20 + (H - 60) * (1 - v / max);
  const chosen = bars.find((b) => b.key === sel);
  const statutory = total(taxes) + total(social);

  return (
    <div className="analysis">
      <section className="card">
        <h3>
          Payslip · {a.period}
          {a.tax_class && <span className="muted"> · tax class {a.tax_class}</span>}
        </h3>
        <p className="muted small">
          From every 100 € gross you keep {((a.net / a.gross) * 100).toFixed(0)} € net. Click a bar to see its lines. Code checked that gross − taxes − social insurance =
          net {a.payout !== null && "and net ± other lines = paid out"}.
        </p>
        <svg viewBox={`0 0 ${W} ${H}`} className="waterfall" role="img" aria-label="From gross to paid out">
          {bars.map((b, i) => {
            const top = yOf(Math.max(b.from, b.to));
            const height = Math.max(Math.abs(yOf(b.from) - yOf(b.to)), 2);
            const down = b.to < b.from;
            return (
              <g key={b.key} className={`wbar ${b.total ? "total" : down ? "down" : "up"} ${sel === b.key ? "on" : ""}`} onClick={() => setSel(b.key)} style={{ cursor: "pointer" }}>
                {i > 0 && <line className="connector" x1={i * bw - bw * 0.2} x2={i * bw + bw * 0.2} y1={yOf(bars[i - 1].to)} y2={yOf(bars[i - 1].to)} />}
                <rect x={i * bw + bw * 0.2} width={bw * 0.6} y={top} height={height} rx={4} />
                <text x={i * bw + bw / 2} y={top - 6} textAnchor="middle" className="wval">
                  {b.total ? eur(b.to) : `${down ? "−" : "+"}${eur(Math.abs(b.from - b.to))}`}
                </text>
                <text x={i * bw + bw / 2} y={H - 18} textAnchor="middle" className="wlabel">
                  {b.label}
                </text>
              </g>
            );
          })}
        </svg>
        <div className="share">
          <span style={{ flex: total(taxes) }} className="s-tax" title={`Taxes ${eur(total(taxes))}`} />
          <span style={{ flex: total(social) }} className="s-social" title={`Social insurance ${eur(total(social))}`} />
          <span style={{ flex: a.net }} className="s-net" title={`Net ${eur(a.net)}`} />
        </div>
        <div className="muted small">
          Taxes {((total(taxes) / a.gross) * 100).toFixed(1)}% · social insurance {((total(social) / a.gross) * 100).toFixed(1)}% · together {eur(statutory)}
        </div>
      </section>

      {chosen && chosen.lines.length > 0 && (
        <section className="card">
          <h3>{chosen.label}</h3>
          <table className="lines">
            <tbody>
              {chosen.lines.map((l, i) => (
                <tr key={i}>
                  <td>
                    <strong>{l.label}</strong>
                    <div className="muted small">{l.explanation_en}</div>
                  </td>
                  <td className="num">{eur(l.amount)}</td>
                  <td>
                    <button className="link" onClick={() => onShowQuote(l.quote)}>
                      on payslip
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {a.worth_checking_en.length > 0 && (
        <section className="card callout">
          <h3>Worth checking</h3>
          <ul className="points">
            {a.worth_checking_en.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- contract timeline

function Contract({ run, a, onShowQuote }: { run: RunView; a: ContractAnalysis; onShowQuote: (q: string) => void }) {
  const cancel = run.deadlines.find((d) => d.due_date && a.end_date && d.due_date <= a.end_date);
  const points = [
    a.start_date && { date: a.start_date, label: "Start", cls: "start" },
    { date: run.today, label: "Today", cls: "today" },
    cancel?.due_date && { date: cancel.due_date, label: "Cancel by", cls: "cancel" },
    a.end_date && { date: a.end_date, label: "Term ends", cls: "end" },
  ].filter(Boolean) as { date: string; label: string; cls: string }[];
  const ts = points.map((p) => Date.parse(p.date));
  const lo = Math.min(...ts);
  const hi = Math.max(...ts);
  const pos = (d: string) => (hi === lo ? 50 : 4 + ((Date.parse(d) - lo) / (hi - lo)) * 92);
  // Labels closer than this (in % of the width) would overlap; alternate them above and below the line.
  const above = new Set<string>();
  [...points]
    .sort((p, q) => pos(p.date) - pos(q.date))
    .reduce<{ below: number; top: number }>(
      (last, p) => {
        if (pos(p.date) - last.below >= 14) return { ...last, below: pos(p.date) };
        above.add(p.cls);
        return { ...last, top: pos(p.date) };
      },
      { below: -100, top: -100 },
    );

  return (
    <div className="analysis">
      <section className="card">
        <h3>{a.contract_type}</h3>
        {a.parties.length > 0 && <p className="muted small">{a.parties.join(" · ")}</p>}
        {points.length > 1 && (
          <div className="timeline" aria-label="Contract timeline">
            <div className="track" />
            {a.start_date && a.end_date && <div className="term" style={{ left: `${pos(a.start_date)}%`, width: `${pos(a.end_date) - pos(a.start_date)}%` }} />}
            {points.map((p) => (
              <div key={p.cls} className={`tick ${p.cls} ${above.has(p.cls) ? "above" : ""}`} style={{ left: `${pos(p.date)}%` }}>
                <span className="tick-dot" />
                <span className="tick-label">
                  {p.label}
                  <small>{fmtDate(p.date, { weekday: undefined })}</small>
                </span>
              </div>
            ))}
          </div>
        )}
        <dl className="facts-grid">
          {a.minimum_term && (
            <>
              <dt>Minimum term</dt>
              <dd>{a.minimum_term}</dd>
            </>
          )}
          {a.notice_period && (
            <>
              <dt>Notice</dt>
              <dd>{a.notice_period}</dd>
            </>
          )}
          {a.renewal && (
            <>
              <dt>If nobody cancels</dt>
              <dd>{a.renewal}</dd>
            </>
          )}
          {a.recurring_costs.length > 0 && (
            <>
              <dt>Costs</dt>
              <dd>{a.recurring_costs.join(" · ")}</dd>
            </>
          )}
        </dl>
      </section>
      <div className="clause-grid">
        {a.watch_out.map((c, i) => (
          <article key={`w${i}`} className="card clause watch">
            <span className="clause-kind">⚠ Watch out</span>
            <h4>{c.title}</h4>
            <p>{c.explanation_en}</p>
            <button className="link" onClick={() => onShowQuote(c.quote)}>
              Read the clause
            </button>
          </article>
        ))}
        {a.key_terms.map((c, i) => (
          <article key={`k${i}`} className="card clause">
            <span className="clause-kind">Key term</span>
            <h4>{c.title}</h4>
            <p>{c.explanation_en}</p>
            <button className="link" onClick={() => onShowQuote(c.quote)}>
              Read the clause
            </button>
          </article>
        ))}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- form checklist

function Form({ runId, a }: { runId: string; a: FormGuide }) {
  const storeKey = `bk-form-${runId}`;
  const [done, setDone] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(localStorage.getItem(storeKey) ?? "{}");
    } catch {
      return {};
    }
  });
  const [open, setOpen] = useState<number | null>(0);
  useEffect(() => {
    try {
      localStorage.setItem(storeKey, JSON.stringify(done));
    } catch {
      /* private mode: ticks just aren't remembered */
    }
  }, [done, storeKey]);
  const items = [...a.fields.map((f) => f.label), ...a.attachments.map((x) => `attach:${x}`)];
  const count = items.filter((k) => done[k]).length;
  const toggle = (k: string) => setDone({ ...done, [k]: !done[k] });

  return (
    <div className="analysis">
      <section className="card">
        <h3>{a.form_name}</h3>
        <p>{a.purpose_en}</p>
        <div className="progress" aria-label={`${count} of ${items.length} done`}>
          <span style={{ width: `${(count / Math.max(items.length, 1)) * 100}%` }} />
        </div>
        <div className="muted small">
          {count} of {items.length} done · ticks are saved in this browser only
        </div>
      </section>
      <section className="card">
        <h3>Fields</h3>
        <ol className="checklist">
          {a.fields.map((f, i) => (
            <li key={i} className={done[f.label] ? "checked" : ""}>
              <input type="checkbox" checked={!!done[f.label]} onChange={() => toggle(f.label)} aria-label={`Done: ${f.label}`} />
              <div className="cl-main">
                <button className="cl-title" onClick={() => setOpen(open === i ? null : i)} aria-expanded={open === i}>
                  {f.label} {f.required && <span className="tag">required</span>}
                </button>
                {open === i && (
                  <div className="cl-detail">
                    <p>{f.explanation_en}</p>
                    <p>
                      <strong>What to enter:</strong> {f.what_to_enter}
                    </p>
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
      </section>
      {(a.attachments.length > 0 || a.watch_out.length > 0 || a.where_to_send) && (
        <section className="card">
          {a.attachments.length > 0 && (
            <>
              <h3>Attach</h3>
              <ul className="checklist">
                {a.attachments.map((x) => (
                  <li key={x} className={done[`attach:${x}`] ? "checked" : ""}>
                    <input type="checkbox" checked={!!done[`attach:${x}`]} onChange={() => toggle(`attach:${x}`)} aria-label={`Attached: ${x}`} />
                    <span>{x}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {a.watch_out.map((w, i) => (
            <div key={i} className="callout">
              ⚠ {w}
            </div>
          ))}
          {a.where_to_send && (
            <p>
              <strong>Send to:</strong> {a.where_to_send}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
