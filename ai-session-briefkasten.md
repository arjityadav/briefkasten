# AI coding session: Briefkasten

**Project:** Briefkasten is a multi-agent LLM assistant for official German mail. It explains a letter, computes its legal deadlines with deterministic code, verifies every fact against the source, and drafts a reply the user approves. It's Python with a custom ReAct runtime, a Supervisor and 9 specialist agents, FastAPI, React/TypeScript and Langfuse.
**Session task (Claude Code, 28–29 Oct 2026):** get the agents running reliably on Groq, and close a privacy gap where scanned letters reached the cloud model before personal data was masked.
**What I decided:** I re-ran a failure myself instead of trusting the AI's diagnosis, and that exposed a wrong model choice. I kept image reading on the local machine. I refused to ship "unlisted names leak" as a known limitation. I kept commits under my own control.

> The log is condensed: my steps are summarised, and the AI's output is shortened (`…`). No keys or personal data appear: all sample letters are fictional ("Max Mustermann").

---

## At a glance: where I steered the AI

| Moment | What the AI proposed or did | What I did | Outcome |
|---|---|---|---|
| Agents failing on Groq | Tune the prompt, add steps, raise reasoning effort | Reproduced it myself on a different letter | Root cause was the model the AI had chosen. Switched it; the run passes |
| Scans and photos | The image went to the cloud *before* redaction | Asked how scans are redacted; chose local OCR | Images never leave the machine by default. An end-to-end OCR check caught a real leak (`Musterstraße` read as `Musterstrake`), fixed with fuzzy redaction |
| Unlisted names | Listed it as a "known limitation" | Rejected that; asked for automatic detection | 0 personal names or streets leak across 11 sample documents, with nothing configured |
| Public hosting | — | Checked the legal risks first (RDG, StBerG, GDPR) | Demo-only public mode plus a serve guard |
| Git | Committed on its own | Corrected it: I commit my own code | Commit undone, nothing lost; the rule saved for future sessions |

---

## Episode 1: Don't trust the diagnosis, reproduce it

**Context:** I asked for a live view of each agent's steps so I could watch real runs. The AI built it with a test (63 passing). Its own background run on Groq failed, and it proposed three fixes: higher reasoning effort, more agent steps, and a stricter prompt.

**My call:** I stopped it and ran a different letter myself before changing anything:

```
0:01  triage     ✗ tool call rejected by the API: … parameters for tool submit did not match schema …
0:03  explainer  ✗ tool call rejected by the API: … attempted to call tool 'json' which was not in request.tools
0:03  ⏳ rate limited, retrying in 8s (attempt 2 of 4)
0:26  run finished: failed — explainer agent did not finish within 6 steps
```

**What it revealed:** the same failure on a second letter pointed to the model, not the prompt. The cause was `gpt-oss` on Groq, the AI's own earlier choice:
- it invents a tool called `json`;
- it drops required fields;
- Groq's docs confirm it can't make parallel tool calls, which the Supervisor depends on.

The three proposed fixes would only have treated symptoms.

**Outcome:**
- Every agent switched to Qwen 3.8.
- Provider-side tool rejections are fed back to the agent as observations instead of crashing the run.
- Rate-limit waits got their own retry budget.
- The re-run passed:
  - both deadlines were legally correct, including the 4-day notification fiction and the weekend shift;
  - 9 quotes were verified against the letter;
  - 64 tests passed.

**Takeaway:** an agent's failure report is a hypothesis. One independent reproduction on a second input separated the root cause from the symptoms.

---

## Episode 2: Privacy has to hold for *every* input

**Context:** redaction runs on text, and a scan has no text until something reads it. I asked how scans and photos were being redacted. The answer was that they weren't: the full image went to the cloud vision model, and personal data was masked only *after* transcription.

**Options the AI laid out:**
- **A:** local Tesseract OCR.
- **B:** a local Ollama vision model.
- **C:** black out the sensitive regions of the image, then send it.

**My call:** A as the default and B as a setting. The cloud is an explicit opt-in that shows a warning, and C was dropped because whatever the local OCR misses still leaks.

**Implementation:**
- `readers.py` with `BRIEFKASTEN_IMAGE_READER = ocr | local_vision | cloud`. The `local_vision` option is refused if the server address isn't local.
- The tests use a fake `tesseract` binary so they run on any machine. One of them failed because of a bug in the fake script, which was fixed (93 passing).

**Verification beyond unit tests:**
- **The test:** the sample letter was rendered as an image and run through the real Tesseract on my machine, then redacted, with no model call.
- **The leak it caught:** without German language data, OCR read `Musterstraße 12` as `Musterstrake 12`. Exact matching missed it, so the street would have reached the provider.
- **The fix:** fuzzy redaction. ß/ss, ä/ae and straße/str. count as equal, and near-matches at 85% similarity or more are masked.
- **On export:** placeholders restore the *correct* spelling, not the OCR's misreading.
- **A guard test** keeps `Musterstadt` unmasked when the listed name is `Mustermann`.
- 95 tests passed.

**Pushing past the "known limitation":** the AI ended its report with *"names you didn't list aren't masked, e.g. a family member in the letter"*. I didn't accept that as the end state and asked for automatic detection.
- **The rules:** detection runs locally, uses German letter conventions and needs no new dependencies. It recognises `Herrn/Frau …`, labels like `Ehefrau:`/`Kind:`/`Kontoinhaber:`, the address-block name line, street plus number, and birthdates.
- **Organisations stay visible,** because the agents need to know who sent the letter.
- **A test caught over-masking:** AOK's own public address had been hidden. Organisation hints were added (99 passing).
- **Final check:** with *nothing* configured, all 11 sample documents were scanned, and no personal name or street leaked.

**Takeaway:** a privacy guarantee is only as strong as its weakest input path. The end-to-end run with the real OCR found the leak, which unit tests with clean text never would have.

---

## Principles I held throughout

- **The LLM extracts, code computes.** Agents only extract a deadline *rule* plus the exact source sentence. Deterministic code applies German law (§§ 187/188/193 BGB, § 108 AO, state holidays). Date arithmetic is never left to the model.
- **Ground everything.** Every quote, amount and date must be found in the letter. If it isn't, the error goes back to the agent to repair, and anything still unresolved is shown to the user.
- **The human stays in control.**
  - **For the user:** a drafted reply can't be approved while it still has blanks, and can't be exported until it's approved.
  - **For me:** when the AI committed on its own, I had it undo the commit and record the rule for future sessions.
- **Check the consequences before shipping.** Before making it public, I reviewed the legal exposure: legal-services law (RDG), tax-advice law (StBerG), GDPR transfers to a US provider, and the Impressum. I shipped a demo-only public mode with a guard against exposing full mode by accident.

## Open items I'm aware of

- A local NER model would catch names that appear only in running prose.
- The local run file stores real values unencrypted.
- A full live run after the drafter fix is still to be confirmed (it was blocked by the provider's daily quota).
- The 11-document eval set needs to grow.
