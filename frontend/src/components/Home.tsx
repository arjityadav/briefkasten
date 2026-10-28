import { useRef, useState } from "react";
import type { Meta } from "../types";

const ACCEPT = ".pdf,.docx,.eml,.txt,.md,.png,.jpg,.jpeg,.webp,.heic,.heif";
const KIND_ICON: Record<string, string> = { letter: "✉", payslip: "€", contract: "§", form: "☐" };

interface Props {
  meta: Meta;
  onStartJob: (form: FormData) => void;
  onStartDemo: (name: string) => void;
  busy: boolean;
}

export function Home({ meta, onStartJob, onStartDemo, busy }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [text, setText] = useState("");
  const [over, setOver] = useState(false);
  const [received, setReceived] = useState("");
  const [intent, setIntent] = useState("");
  const [state, setState] = useState(meta.default_state);
  const input = useRef<HTMLInputElement>(null);

  const add = (list: FileList | null) => list && setFiles((f) => [...f, ...Array.from(list)]);
  const move = (i: number, d: number) =>
    setFiles((f) => {
      const next = [...f];
      [next[i], next[i + d]] = [next[i + d], next[i]];
      return next;
    });
  const ready = files.length > 0 || text.trim().length > 20;

  const submit = () => {
    const fd = new FormData();
    files.forEach((f) => fd.append("file", f));
    if (!files.length) fd.append("text", text);
    if (received) fd.append("received_on", received);
    if (intent) fd.append("intent", intent);
    fd.append("bundesland", state);
    onStartJob(fd);
  };

  return (
    <div className="home">
      <section className="hero">
        <h1>Understand official German mail.</h1>
        <p className="lead">
          A team of AI agents explains the letter, works out every deadline with the actual legal rules, checks its own work against the document, and
          drafts a reply you approve. Your personal details are masked before any AI sees the text.
        </p>
      </section>

      <div className="home-grid">
        {!meta.demo_only && (
          <section className="card upload">
            <h2>Your document</h2>
            <div
              className={`drop ${over ? "over" : ""}`}
              onClick={() => input.current?.click()}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setOver(true);
              }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setOver(false);
                add(e.dataTransfer.files);
              }}
              tabIndex={0}
              role="button"
            >
              <div className="drop-icon">⇪</div>
              <strong>Drop files here or click to choose</strong>
              <span className="muted small">PDF, Word, e-mail (.eml) or photos — several photos are read as one document, in order</span>
              <input ref={input} type="file" accept={ACCEPT} multiple hidden onChange={(e) => add(e.target.files)} />
            </div>
            {files.length > 0 && (
              <ol className="files">
                {files.map((f, i) => (
                  <li key={`${f.name}-${i}`}>
                    <span className="page">{i + 1}</span>
                    <span className="fname">{f.name}</span>
                    <span className="muted small">{(f.size / 1024).toFixed(0)} KB</span>
                    <button className="icon" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up">
                      ↑
                    </button>
                    <button className="icon" disabled={i === files.length - 1} onClick={() => move(i, 1)} aria-label="Move down">
                      ↓
                    </button>
                    <button className="icon" onClick={() => setFiles(files.filter((_, j) => j !== i))} aria-label="Remove">
                      ✕
                    </button>
                  </li>
                ))}
              </ol>
            )}
            {!files.length && (
              <>
                <div className="or">or paste the text</div>
                <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Sehr geehrte Damen und Herren, …" rows={5} />
              </>
            )}
            <div className="options">
              <label>
                Received on <span className="muted small">(date on the yellow envelope, if any)</span>
                <input type="date" value={received} onChange={(e) => setReceived(e.target.value)} />
              </label>
              <label>
                Bundesland
                <select value={state} onChange={(e) => setState(e.target.value)}>
                  {Object.entries(meta.states).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Draft a reply
                <select value={intent} onChange={(e) => setIntent(e.target.value)}>
                  <option value="">Only if the document needs one</option>
                  {Object.entries(meta.intents).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <button className="primary big" disabled={!ready || busy} onClick={submit}>
              {busy ? "Starting…" : "Analyse with the agents →"}
            </button>
            <p className="muted small">
              Scans are read by {meta.image_reader === "cloud" ? <strong className="warn-text">the cloud model (unredacted)</strong> : "local OCR on the server"}.
            </p>
          </section>
        )}

        <section className={`samples ${meta.demo_only ? "wide" : ""}`}>
          <h2>{meta.demo_only ? "Pick a sample and watch the agents work" : "Or watch a sample live"}</h2>
          <p className="muted small">Fictional documents. The agents' decisions were recorded; every tool, check and deadline calculation runs live.</p>
          <div className="sample-grid">
            {meta.demos.map((name) => {
              const d = meta.demo_descriptions[name] ?? { title: name, kind: "letter", blurb: "" };
              return (
                <button key={name} className="sample" onClick={() => onStartDemo(name)} disabled={busy}>
                  <span className={`kind k-${d.kind}`}>{KIND_ICON[d.kind] ?? "✉"}</span>
                  <span className="sample-title">{d.title}</span>
                  <span className="muted small">{d.blurb}</span>
                  <span className="play">▶ Watch live</span>
                </button>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
}
