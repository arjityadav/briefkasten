import { useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { BLANK, blanks, placeholderLabel, pretty } from "../text";
import type { Draft, RunView } from "../types";

interface Props {
  run: RunView;
  draft: Draft;
  onRunChange: (run: RunView) => void;
}

export function DraftView({ run, draft, onRunChange }: Props) {
  const [subject, setSubject] = useState(draft.subject_de);
  const [body, setBody] = useState(draft.body_de);
  const [fills, setFills] = useState<Record<string, string>>({});
  const [english, setEnglish] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputs = useRef<Record<string, HTMLInputElement | null>>({});
  const editable = draft.status === "pending_approval";
  const open = blanks(`${subject}\n${body}`);

  const fill = (blank: string) => {
    const value = fills[blank]?.trim();
    if (!value) return;
    setBody((b) => b.split(blank).join(value));
    setSubject((s) => s.split(blank).join(value));
  };

  const act = async (fn: () => Promise<RunView>) => {
    setBusy(true);
    setError(null);
    try {
      onRunChange(await fn());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="draft">
      <section className="card draft-edit">
        <div className="draft-head">
          <h3>
            Reply: {pretty(draft.intent)} <span className={`pill s-${draft.status}`}>{pretty(draft.status)}</span>
          </h3>
          <label className="switch">
            <input type="checkbox" checked={english} onChange={(e) => setEnglish(e.target.checked)} /> English side by side
          </label>
        </div>
        <p className="muted small">{draft.notes_en}</p>

        {editable && open.length > 0 && (
          <div className="blanks">
            <strong>
              Fill in {open.length} blank{open.length === 1 ? "" : "s"} — only you know these
            </strong>
            {open.map((b) => (
              <div key={b} className="blank-row">
                <label htmlFor={`blank-${b}`}>{b.slice(1, -1)}</label>
                <input
                  id={`blank-${b}`}
                  ref={(el) => (inputs.current[b] = el)}
                  value={fills[b] ?? ""}
                  onChange={(e) => setFills({ ...fills, [b]: e.target.value })}
                  onKeyDown={(e) => e.key === "Enter" && fill(b)}
                  placeholder="Type, then press Enter"
                />
                <button className="secondary" disabled={!fills[b]?.trim()} onClick={() => fill(b)}>
                  Insert
                </button>
              </div>
            ))}
          </div>
        )}

        <div className={`draft-cols ${english ? "two" : ""}`}>
          <div>
            <label className="field">
              Betreff
              <input value={subject} onChange={(e) => setSubject(e.target.value)} disabled={!editable} />
            </label>
            <label className="field">
              Brief (German)
              <textarea value={body} onChange={(e) => setBody(e.target.value)} disabled={!editable} rows={14} />
            </label>
          </div>
          {english && (
            <div className="english">
              <span className="muted small">English translation of the original draft</span>
              <p>{draft.body_en}</p>
            </div>
          )}
        </div>

        {error && <div className="error-box">{error}</div>}
        {editable && !rejecting && (
          <div className="row-actions">
            <button
              className="primary"
              disabled={busy || open.length > 0}
              title={open.length ? "Fill in every blank first" : undefined}
              onClick={() => act(() => api.approve(run.run_id, subject, body))}
            >
              {open.length ? `Approve (${open.length} blank${open.length === 1 ? "" : "s"} left)` : "Approve reply"}
            </button>
            <button className="secondary" onClick={() => setRejecting(true)}>
              Reject
            </button>
          </div>
        )}
        {rejecting && (
          <div className="row-actions">
            <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why? (optional)" autoFocus />
            <button className="danger" disabled={busy} onClick={() => act(() => api.reject(run.run_id, reason))}>
              Reject draft
            </button>
            <button className="link" onClick={() => setRejecting(false)}>
              Cancel
            </button>
          </div>
        )}
        {draft.status === "approved" && (
          <div className="success">
            Approved. Your real name, address and reference numbers are put back only now, in the downloaded letter.
            <a className="primary button" href={api.letterUrl(run.run_id)}>
              Download letter
            </a>
          </div>
        )}
        {draft.status === "rejected" && <div className="muted">Rejected{draft.rejection_reason ? `: ${draft.rejection_reason}` : ""}.</div>}
      </section>

      <section className="card paper" aria-label="Letter preview">
        <div className="paper-sender">
          <span className="ph">your name</span>
          <br />
          <span className="ph">your address</span>
        </div>
        <div className="paper-to">{run.triage?.sender_name ?? "Recipient"}</div>
        <div className="paper-subject">{preview(subject, (b) => inputs.current[b]?.focus())}</div>
        <div className="paper-body">{preview(body, (b) => inputs.current[b]?.focus())}</div>
        <div className="paper-close">
          Mit freundlichen Grüßen
          <br />
          <br />
          <span className="ph">your signature</span>
        </div>
      </section>
    </div>
  );
}

/** Letter text with masked values as chips and open blanks as clickable amber marks. */
function preview(text: string, onBlank: (b: string) => void): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(BLANK)) {
    out.push(text.slice(last, m.index));
    const isMasked = /^\[[A-Z_]+_\d+\]$/.test(m[0]);
    out.push(
      isMasked ? (
        <span key={key++} className="ph" title="Masked value, restored when you download the approved letter">
          {placeholderLabel(m[0])}
        </span>
      ) : (
        <button key={key++} className="blank-mark" onClick={() => onBlank(m[0])} title="Click to fill in">
          {m[0]}
        </button>
      ),
    );
    last = m.index! + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}
