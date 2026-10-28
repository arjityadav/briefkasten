import { useCallback, useEffect, useRef, useState } from "react";
import { emptyLive, liveFromRun, reduceLive, TOOL_AGENT, type LiveState } from "./agents";
import { api, followJob } from "./api";
import { AgentGraph, agentLabel } from "./components/AgentGraph";
import { Home } from "./components/Home";
import { LiveLog } from "./components/LiveLog";
import { ResultView } from "./components/ResultView";
import { fmtDate } from "./text";
import type { Meta, RunSummary, RunView } from "./types";

type Theme = "light" | "dark";

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const saved = localStorage.getItem("bk-theme");
      if (saved === "light" || saved === "dark") return saved;
    } catch {
      /* storage blocked */
    }
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("bk-theme", theme);
    } catch {
      /* storage blocked */
    }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [live, setLive] = useState<LiveState>(emptyLive);
  const [run, setRun] = useState<RunView | null>(null);
  const [view, setView] = useState<"home" | "live" | "result">("home");
  const [recent, setRecent] = useState<RunSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [theme, toggleTheme] = useTheme();
  const stop = useRef<(() => void) | null>(null);

  const loadRecent = useCallback(() => api.runs().then(setRecent).catch(() => setRecent([])), []);

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setToast(`Could not reach the server: ${e.message}`));
    loadRecent();
    const id = location.hash.match(/^#run\/(.+)$/)?.[1];
    if (id) openRun(id);
    return () => stop.current?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const follow = (jobId: string) => {
    stop.current?.();
    setLive(emptyLive());
    setRun(null);
    setFilter(null);
    setView("live");
    stop.current = followJob(jobId, (e) => {
      setLive((prev) => reduceLive(prev, e));
      if (e.type === "done") {
        setRun(e.run);
        history.replaceState(null, "", `#run/${e.run.run_id}`);
        loadRecent();
      }
      if (e.type === "error") setToast(e.detail);
    });
  };

  const start = async (fn: () => Promise<{ job_id: string }>) => {
    setBusy(true);
    try {
      follow((await fn()).job_id);
    } catch (e) {
      setToast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  async function openRun(id: string) {
    try {
      const r = await api.run(id);
      stop.current?.();
      setRun(r);
      setLive(liveFromRun(r));
      setView("result");
      history.replaceState(null, "", `#run/${id}`);
    } catch (e) {
      setToast((e as Error).message);
    }
  }

  const showResult = useCallback(() => setView("result"), []);

  const goHome = () => {
    stop.current?.();
    setView("home");
    history.replaceState(null, "", location.pathname);
  };

  return (
    <div className="app">
      <header className="top">
        <button className="brand" onClick={goHome}>
          <span className="slot" aria-hidden="true" />
          Briefkasten
        </button>
        {meta?.demo_only && <span className="pill">public demo · sample documents only</span>}
        <span className="spacer" />
        {view !== "home" && (
          <button className="secondary" onClick={goHome}>
            + New document
          </button>
        )}
        <button className="icon theme" onClick={toggleTheme} aria-label="Toggle dark mode" title="Toggle dark mode">
          {theme === "dark" ? "☀" : "☾"}
        </button>
      </header>

      <div className={`layout ${recent.length ? "with-side" : ""}`}>
        {recent.length > 0 && (
          <aside className="side">
            <h3>Recent</h3>
            {recent.map((r) => (
              <button key={r.run_id} className={`recent ${run?.run_id === r.run_id ? "on" : ""}`} onClick={() => openRun(r.run_id)}>
                <span className={`dot u-${r.urgency}`} />
                <span className="recent-main">
                  <strong>{r.sender ?? r.run_id}</strong>
                  <span className="muted small">
                    {r.status.replace("_", " ")} · next {r.next_deadline ? fmtDate(r.next_deadline, { weekday: undefined, year: undefined }) : "–"}
                  </span>
                </span>
              </button>
            ))}
          </aside>
        )}

        <main>
          {!meta && <div className="card muted">Loading…</div>}
          {meta && view === "home" && <Home meta={meta} busy={busy} onStartJob={(fd) => start(() => api.startJob(fd))} onStartDemo={(n) => start(() => api.startDemo(n))} />}
          {view === "live" && <LiveScreen live={live} filter={filter} onFilter={setFilter} onShowResult={showResult} />}
          {view === "result" && run && <ResultView run={run} live={live} onRunChange={(r) => (setRun(r), loadRecent())} />}
        </main>
      </div>

      <footer>
        Briefkasten explains documents with AI agents and checks them with code. It is not legal or tax advice.
        {meta?.imprint_url && (
          <>
            {" "}
            · <a href={meta.imprint_url}>Impressum</a>
          </>
        )}
        {meta?.privacy_url && (
          <>
            {" "}
            · <a href={meta.privacy_url}>Datenschutz</a>
          </>
        )}
      </footer>
      {toast && (
        <div className="toast" role="alert" onClick={() => setToast(null)}>
          {toast}
        </div>
      )}
    </div>
  );
}

function LiveScreen({ live, filter, onFilter, onShowResult }: { live: LiveState; filter: string | null; onFilter: (a: string | null) => void; onShowResult: () => void }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (live.finished) return;
    const t0 = performance.now();
    const id = setInterval(() => setElapsed((performance.now() - t0) / 1000), 200);
    return () => clearInterval(id);
  }, [live.finished]);

  // When the run finishes, move on to the results after a beat, so the final state is visible.
  useEffect(() => {
    if (!live.run) return;
    const t = setTimeout(onShowResult, 1800);
    return () => clearTimeout(t);
  }, [live.run, onShowResult]);

  const lastSup = [...live.events].reverse().find((e) => e.type === "decided" && e.agent === "supervisor");
  const delegated = lastSup && lastSup.type === "decided" ? lastSup.tools.map((t) => TOOL_AGENT[t]).filter(Boolean) : [];
  const steps = live.events.filter((e) => e.type === "decided").length;

  return (
    <div className="live">
      <div className="live-head">
        <div>
          <h2>{live.finished ? (live.error ? "The run failed" : "Done") : "Agents at work"}</h2>
          <p className="muted">
            {live.error
              ? live.error
              : live.finished
                ? "Opening the results…"
                : delegated.length
                  ? `The supervisor delegated to ${delegated.map(agentLabel).join(" and ")}.`
                  : "The supervisor is planning the work."}
          </p>
        </div>
        <div className="live-stats">
          <span>
            <strong>{steps}</strong> steps
          </span>
          <span>
            <strong>{live.finished ? (live.events.at(-1)?.t ?? 0).toFixed(1) : elapsed.toFixed(1)}</strong> s
          </span>
          {live.run && (
            <button className="primary" onClick={onShowResult}>
              See results →
            </button>
          )}
        </div>
      </div>
      <div className="live-grid">
        <AgentGraph live={live} selected={filter} onSelect={onFilter} />
        <LiveLog live={live} filter={filter} onFilter={onFilter} />
      </div>
    </div>
  );
}
