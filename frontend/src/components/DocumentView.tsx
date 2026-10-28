import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { eur, findQuote, fmtDate, PLACEHOLDER, placeholderLabel, type Range } from "../text";
import type { RunView } from "../types";

interface Fact {
  group: string;
  label: string;
  detail?: string;
  quote: string | null;
}

function factsOf(run: RunView): Fact[] {
  const out: Fact[] = [];
  const x = run.extraction;
  if (x?.letter_date) out.push({ group: "Letter", label: "Letter date", detail: fmtDate(x.letter_date), quote: x.letter_date_quote });
  x?.deadlines.forEach((d) => out.push({ group: "Deadlines", label: d.purpose, detail: d.rule.kind.replace(/_/g, " "), quote: d.quote }));
  x?.amounts.forEach((a) => out.push({ group: "Amounts", label: a.purpose, detail: `${eur(a.value)} · ${a.direction.replace("_", " ")}`, quote: a.quote }));
  x?.references.forEach((r) => out.push({ group: "References", label: r.label, detail: r.value, quote: r.quote }));
  x?.actions.forEach((a) => a.quote && out.push({ group: "To do", label: a.description, quote: a.quote }));
  const an = run.analysis;
  if (an?.family === "payslip") {
    out.push({ group: "Payslip", label: "Gross", detail: eur(an.gross), quote: an.gross_quote });
    an.lines.forEach((l) => out.push({ group: "Payslip", label: l.label, detail: eur(l.amount), quote: l.quote }));
    out.push({ group: "Payslip", label: "Net", detail: eur(an.net), quote: an.net_quote });
    if (an.payout !== null) out.push({ group: "Payslip", label: "Paid out", detail: eur(an.payout), quote: an.payout_quote });
  }
  if (an?.family === "contract") [...an.key_terms, ...an.watch_out].forEach((c) => out.push({ group: "Contract", label: c.title, quote: c.quote }));
  if (an?.family === "form") an.fields.forEach((f) => out.push({ group: "Form fields", label: f.label, detail: f.what_to_enter, quote: f.label }));
  return out;
}

interface Props {
  run: RunView;
  focus: string | null;
  onFocus: (q: string | null) => void;
}

export function DocumentView({ run, focus, onFocus }: Props) {
  const [search, setSearch] = useState("");
  const text = run.text_redacted;
  const facts = useMemo(() => factsOf(run), [run]);
  const groups = [...new Set(facts.map((f) => f.group))];
  const docRef = useRef<HTMLPreElement>(null);

  const focusRanges = useMemo(() => findQuote(text, focus), [text, focus]);
  const searchRanges = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (q.length < 2) return [];
    const out: Range[] = [];
    const lower = text.toLowerCase();
    for (let i = lower.indexOf(q); i !== -1; i = lower.indexOf(q, i + 1)) out.push({ start: i, end: i + q.length });
    return out;
  }, [text, search]);

  useEffect(() => {
    docRef.current?.querySelector(".mark-focus")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focusRanges]);

  return (
    <div className="docview">
      <aside className="facts">
        <p className="muted small">Every fact the agents extracted comes with a quote. Hover or click one to see it in the document.</p>
        {groups.map((g) => (
          <section key={g}>
            <h4>{g}</h4>
            {facts
              .filter((f) => f.group === g)
              .map((f, i) => {
                const found = findQuote(text, f.quote).length > 0;
                return (
                  <button
                    key={i}
                    className={`fact ${focus && focus === f.quote ? "on" : ""}`}
                    onMouseEnter={() => onFocus(f.quote)}
                    onFocus={() => onFocus(f.quote)}
                    onClick={() => onFocus(f.quote)}
                  >
                    <span className={`found ${found ? "yes" : "no"}`} title={found ? "quote found in the document" : "quote not found"}>
                      {found ? "✓" : "✗"}
                    </span>
                    <span className="fact-label">{f.label}</span>
                    {f.detail && <span className="muted small">{f.detail}</span>}
                  </button>
                );
              })}
          </section>
        ))}
        {!facts.length && <p className="muted">No facts were extracted.</p>}
      </aside>

      <section className="card doc">
        <div className="doc-head">
          <div>
            <strong>Exactly what the AI agents saw</strong>
            <div className="muted small">
              {run.redaction_count} personal identifiers were replaced before any model call. The real values stay on the server and only return when you
              export an approved reply.
            </div>
          </div>
          <input className="search" type="search" placeholder="Search the document…" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        {search.trim().length >= 2 && (
          <div className="muted small">
            {searchRanges.length} match{searchRanges.length === 1 ? "" : "es"}
          </div>
        )}
        <pre className="doc-text" ref={docRef}>
          {renderDoc(text, focusRanges, searchRanges)}
        </pre>
      </section>
    </div>
  );
}

function renderDoc(text: string, focus: Range[], search: Range[]): ReactNode[] {
  const flags = new Uint8Array(text.length); // 1 = focused quote, 2 = search hit
  focus.forEach((r) => flags.fill(1, r.start, r.end));
  search.forEach((r) => {
    for (let i = r.start; i < r.end; i++) flags[i] |= 2;
  });
  const placeholders = new Map<number, string>();
  for (const m of text.matchAll(PLACEHOLDER)) placeholders.set(m.index!, m[0]);

  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < text.length) {
    const ph = placeholders.get(i);
    if (ph) {
      out.push(
        <span key={key++} className={`ph ${flags[i] & 1 ? "mark-focus" : ""}`} title={`${ph}: masked before any AI saw the text`}>
          {placeholderLabel(ph)}
        </span>,
      );
      i += ph.length;
      continue;
    }
    let j = i + 1;
    while (j < text.length && flags[j] === flags[i] && !placeholders.has(j)) j++;
    const chunk = text.slice(i, j);
    const f = flags[i];
    out.push(f ? <mark key={key++} className={`${f & 1 ? "mark-focus" : ""} ${f & 2 ? "mark-search" : ""}`}>{chunk}</mark> : chunk);
    i = j;
  }
  return out;
}
