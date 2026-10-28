import type { JobEvent, Meta, RunSummary, RunView } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  const isJson = res.headers.get("content-type")?.includes("json");
  const body = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = isJson && body && typeof body === "object" && "detail" in body ? String(body.detail) : String(body);
    throw new Error(detail || res.statusText);
  }
  return body as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  meta: () => request<Meta>("/api/meta"),
  runs: () => request<RunSummary[]>("/api/runs"),
  run: (id: string) => request<RunView>(`/api/runs/${id}`),
  startDemo: (name: string, pace = 0.7) => request<{ job_id: string }>(`/api/demo/${name}/job?pace=${pace}`, { method: "POST" }),
  startJob: (form: FormData) => request<{ job_id: string }>("/api/jobs", { method: "POST", body: form }),
  approve: (id: string, subject_de: string, body_de: string) => request<RunView>(`/api/runs/${id}/approve`, json({ subject_de, body_de })),
  reject: (id: string, reason: string) => request<RunView>(`/api/runs/${id}/reject`, json({ reason })),
  letterUrl: (id: string) => `/api/runs/${id}/letter`,
};

const EVENT_TYPES: JobEvent["type"][] = ["started", "decided", "problem", "info", "done", "error"];

/** Follow a job's Server-Sent Events. EventSource reconnects by itself and resumes from the last event id. */
export function followJob(jobId: string, onEvent: (e: JobEvent) => void): () => void {
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  const handle = (msg: MessageEvent<string>) => {
    const event = JSON.parse(msg.data) as JobEvent;
    onEvent(event);
    if (event.type === "done" || event.type === "error") source.close();
  };
  EVENT_TYPES.forEach((t) => source.addEventListener(t, handle as EventListener));
  return () => source.close();
}
