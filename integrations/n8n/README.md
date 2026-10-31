# Briefkasten + n8n

An n8n workflow that sends new official mail to Briefkasten, puts every deadline in your calendar and
e-mails you a summary:

```
New e-mail (IMAP) ──┐                         ┌─ yes → Analyse the file ─┐
                    ├─→ Has a file? ──────────┤                          ├─→ Wait 30 s ─→ Check the run ─→ Finished?
Upload (webhook) ───┘                         └─ no ──→ Analyse the text ┘       ▲                            │
                                                                                 └──────────── no ────────────┤
                                                                                                              │ yes
                                        ┌──────────────────────────── Succeeded? ◄────────────────────────────┘
                                        ├─ yes → One item per deadline → Add to Google Calendar
                                        ├─ yes → E-mail me the summary
                                        └─ no ─→ E-mail me the failure
```

## Run it

```bash
cp .env.template .env           # model keys, BRIEFKASTEN_STATE, names to mask (optional: LANGFUSE_*)
docker compose up -d --build
```

- n8n: http://127.0.0.1:5678 (create the owner account on first visit)
- Briefkasten UI: http://127.0.0.1:8000

In n8n: **Workflows → Import from file → `integrations/n8n/briefkasten-inbox.json`**. Then open the three nodes
whose names say *set credentials* and add:

| Node | Credential |
|---|---|
| New e-mail | IMAP account (a dedicated inbox for official mail works best: forward letters there, or scan to it) |
| Add to Google Calendar | Google Calendar OAuth2 |
| E-mail me the summary / failure | SMTP account, and your own address in *To* |

Set `bundesland` in the two *Analyse* nodes to your state, then activate the workflow.

Try it without e-mail:

```bash
curl -F file=@src/briefkasten/samples/letters/mahnbescheid.txt http://127.0.0.1:5678/webhook/briefkasten
```

## What Briefkasten provides for this

- `POST /api/jobs` starts a run (multipart: `file` or `text`, optional `received_on`, `intent`, `bundesland`).
- `GET /api/jobs/{id}` is plain JSON for polling. When `done`, it includes `calendar_events` (title, date,
  description with the legal working) and `summary_text` (a ready-to-send notification).

Only the first attachment of an e-mail is analysed. Deadlines for formally served documents (Mahnbescheid,
Bußgeldbescheid) count from the date on the yellow envelope, which an e-mail can't know: they arrive marked
*estimated*, using the letter date as the earliest possible start. Re-run those in the Briefkasten UI with the
envelope date.

## Privacy

- Self-host n8n (this compose file does). n8n handles the unredacted letter before Briefkasten masks it, so a hosted
  n8n would see your personal data.
- Both services listen on 127.0.0.1 only. Briefkasten runs in full mode here and has no login: don't publish its port.
- Calendar events and the summary e-mail contain the redacted view (placeholders like `[NAME_1]`), not your real data.
