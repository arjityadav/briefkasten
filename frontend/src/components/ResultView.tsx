import { useEffect, useState } from "react";
import type { LiveState } from "../agents";
import { pretty } from "../text";
import type { RunView } from "../types";
import { AgentGraph } from "./AgentGraph";
import { Analysis } from "./Analysis";
import { Deadlines } from "./Deadlines";
import { DocumentView } from "./DocumentView";
import { DraftView } from "./DraftView";
import { LiveLog } from "./LiveLog";
import { Checks, Overview } from "./Panels";

type Tab = "overview" | "deadlines" | "analysis" | "document" | "draft" | "checks" | "agents";

const capitalise = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

const URGENCY: Record<RunView["urgency"], string> = { overdue: "Overdue", urgent: "Urgent", soon: "Due soon", relaxed: "No rush", none: "No deadline" };

interface Props {
  run: RunView;
  live: LiveState;
  onRunChange: (run: RunView) => void;
}

export function ResultView({ run, live, onRunChange }: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const [focus, setFocus] = useState<string | null>(null);
  const [filter, setFilter] = useState<string | null>(null);

  useEffect(() => setTab("overview"), [run.run_id]);

  const showQuote = (quote: string | null | undefined) => {
    if (!quote) return;
    setFocus(quote);
    setTab("document");
  };

  const tabs: { id: Tab; label: string; badge?: string | number; hidden?: boolean }[] = [
    { id: "overview", label: "Overview" },
    { id: "deadlines", label: "Deadlines", badge: run.deadlines.length || undefined, hidden: !run.deadlines.length },
    { id: "analysis", label: run.analysis ? capitalise(pretty(run.analysis.family)) : "Analysis", hidden: !run.analysis },
    { id: "document", label: "Document" },
    { id: "draft", label: "Reply draft", badge: run.draft?.status === "pending_approval" ? "!" : undefined, hidden: !run.draft },
    { id: "checks", label: "Checks", badge: run.verification ? (run.verification.passed ? "✓" : "✗") : undefined },
    { id: "agents", label: "How it was made" },
  ];

  const t = run.triage;
  return (
    <div className="result">
      <header className={`banner u-${run.urgency}`}>
        <span className="badge-urgency">{URGENCY[run.urgency]}</span>
        <div className="banner-main">
          <h2>{t?.sender_name ?? run.source_name}</h2>
          <div className="muted small">
            {t ? pretty(t.doc_type) : "document"} · {run.redaction_count} identifiers masked · {run.bundesland} · run {run.run_id}
          </div>
        </div>
        {run.status === "failed" && <span className="pill err">failed</span>}
        {run.status === "awaiting_approval" && (
          <button className="pill action" onClick={() => setTab("draft")}>
            Reply waiting for approval →
          </button>
        )}
      </header>
      {run.error && <div className="card error-box">{run.error}</div>}
      {run.image_reader && (
        <div className={`card note ${run.image_reader === "cloud" ? "warn" : ""}`}>
          Scan read by{" "}
          {run.image_reader === "cloud" ? "the cloud vision model: the unredacted image was sent to the AI provider." : "a reader on the server: the image never reached an AI provider."}
        </div>
      )}

      <nav className="tabs" role="tablist">
        {tabs
          .filter((x) => !x.hidden)
          .map((x) => (
            <button key={x.id} role="tab" aria-selected={tab === x.id} className={tab === x.id ? "on" : ""} onClick={() => setTab(x.id)}>
              {x.label}
              {x.badge !== undefined && <span className="tab-badge">{x.badge}</span>}
            </button>
          ))}
      </nav>

      <div className="tab-body" role="tabpanel">
        {tab === "overview" && <Overview run={run} onShowQuote={showQuote} goTo={(x) => setTab(x as Tab)} />}
        {tab === "deadlines" && <Deadlines run={run} onShowQuote={showQuote} />}
        {tab === "analysis" && run.analysis && <Analysis run={run} analysis={run.analysis} onShowQuote={showQuote} />}
        {tab === "document" && <DocumentView run={run} focus={focus} onFocus={setFocus} />}
        {tab === "draft" && run.draft && <DraftView run={run} draft={run.draft} onRunChange={onRunChange} />}
        {tab === "checks" && <Checks run={run} />}
        {tab === "agents" && (
          <div className="live-grid">
            <AgentGraph live={live} selected={filter} onSelect={setFilter} />
            <LiveLog live={live} filter={filter} onFilter={setFilter} />
          </div>
        )}
      </div>
    </div>
  );
}
