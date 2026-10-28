import { useMemo, useState } from "react";
import { daysLabel, download, fmtDate, icsFor } from "../text";
import type { ResolvedDeadline, RunView } from "../types";

interface Props {
  run: RunView;
  onShowQuote: (q: string) => void;
}

const BASIS: Record<ResolvedDeadline["basis"], string> = {
  stated: "date stated in the letter",
  computed: "computed from the legal rules",
  estimated: "estimated — give the receipt date for an exact result",
  unknown: "could not be computed",
};

const heat = (d: ResolvedDeadline) =>
  d.days_left === null ? "unknown" : d.days_left < 0 ? "overdue" : d.days_left <= 7 ? "urgent" : d.days_left <= 21 ? "soon" : "relaxed";

export function Deadlines({ run, onShowQuote }: Props) {
  const [selected, setSelected] = useState<string | null>(run.deadlines[0]?.id ?? null);
  const dated = run.deadlines.filter((d) => d.due_date);
  const sender = run.triage?.sender_name ?? "letter";

  const exportAll = () =>
    download(
      `deadlines-${run.run_id}.ics`,
      icsFor(dated.map((d) => ({ title: `${d.purpose} (${sender})`, date: d.due_date!, description: d.steps.join("\n") }))),
      "text/calendar",
    );

  return (
    <div className="deadlines">
      <div className="deadline-list">
        {run.deadlines.map((d) => (
          <article
            key={d.id}
            className={`card deadline h-${heat(d)} ${selected === d.id ? "selected" : ""}`}
            onClick={() => setSelected(d.id)}
            onMouseEnter={() => setSelected(d.id)}
          >
            <div className="countdown">
              <span className="num">{d.days_left === null ? "?" : Math.abs(d.days_left)}</span>
              <span className="unit">{d.days_left === null ? "" : d.days_left < 0 ? "days ago" : d.days_left === 1 ? "day left" : "days left"}</span>
            </div>
            <div className="deadline-main">
              <h3>{d.purpose}</h3>
              <div className="date-line">
                <strong>{fmtDate(d.due_date, { weekday: "long" })}</strong> <span className="muted">· {daysLabel(d.days_left)}</span>
                <span className={`tag b-${d.basis}`} title={BASIS[d.basis]}>
                  {d.basis}
                </span>
              </div>
              <details>
                <summary>How this date was worked out</summary>
                <ol className="steps">
                  {d.steps.map((s, i) => (
                    <li key={i} style={{ animationDelay: `${i * 80}ms` }}>
                      {s}
                    </li>
                  ))}
                </ol>
              </details>
              <div className="row-actions">
                <button className="link" onClick={() => onShowQuote(d.quote)}>
                  Show in letter
                </button>
                {d.due_date && (
                  <button
                    className="link"
                    onClick={() =>
                      download(`${d.id}.ics`, icsFor([{ title: `${d.purpose} (${sender})`, date: d.due_date!, description: d.steps.join("\n") }]), "text/calendar")
                    }
                  >
                    Add to calendar
                  </button>
                )}
              </div>
            </div>
          </article>
        ))}
        {dated.length > 1 && (
          <button className="secondary" onClick={exportAll}>
            Add all {dated.length} deadlines to your calendar (.ics, reminder 3 days before)
          </button>
        )}
      </div>
      <Calendar run={run} selected={selected} onSelect={setSelected} />
    </div>
  );
}

function Calendar({ run, selected, onSelect }: { run: RunView; selected: string | null; onSelect: (id: string) => void }) {
  const today = run.today;
  const sel = run.deadlines.find((d) => d.id === selected && d.due_date);
  const [cursor, setCursor] = useState(() => (sel?.due_date ?? today).slice(0, 7));
  const byDate = useMemo(() => {
    const m: Record<string, ResolvedDeadline[]> = {};
    run.deadlines.forEach((d) => d.due_date && (m[d.due_date] ??= []).push(d));
    return m;
  }, [run.deadlines]);

  // keep the calendar on the selected deadline's month
  const want = sel?.due_date?.slice(0, 7);
  const [lastWant, setLastWant] = useState(want);
  if (want && want !== lastWant) {
    setLastWant(want);
    setCursor(want);
  }

  const [y, m] = cursor.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1));
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const lead = (first.getUTCDay() + 6) % 7; // Monday first
  const cells = [...Array(lead).fill(null), ...Array.from({ length: days }, (_, i) => i + 1)];
  const shift = (d: number) => {
    const t = new Date(Date.UTC(y, m - 1 + d, 1));
    setCursor(t.toISOString().slice(0, 7));
  };
  const iso = (day: number) => `${cursor}-${String(day).padStart(2, "0")}`;

  return (
    <aside className="card calendar">
      <div className="cal-head">
        <button className="icon" onClick={() => shift(-1)} aria-label="Previous month">
          ‹
        </button>
        <strong>{first.toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" })}</strong>
        <button className="icon" onClick={() => shift(1)} aria-label="Next month">
          ›
        </button>
      </div>
      <div className="cal-grid">
        {["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].map((d) => (
          <span key={d} className="dow">
            {d}
          </span>
        ))}
        {cells.map((day, i) => {
          if (day === null) return <span key={i} />;
          const date = iso(day);
          const due = byDate[date];
          const cls = ["day", date === today ? "today" : "", i % 7 >= 5 ? "weekend" : "", due ? `due h-${heat(due[0])}` : "", due?.some((d) => d.id === selected) ? "sel" : ""];
          return (
            <button key={i} className={cls.join(" ")} disabled={!due} onClick={() => due && onSelect(due[0].id)} title={due?.map((d) => d.purpose).join("\n")}>
              {day}
            </button>
          );
        })}
      </div>
      <div className="legend muted small">
        <span className="dot today-dot" /> today ({fmtDate(today, { weekday: undefined })}) <span className="dot due-dot" /> deadline
      </div>
    </aside>
  );
}
