// Text helpers: finding a quote in the document the way the backend's verifier does
// (whitespace, case and typographic quotes ignored; "..." skips words), plus formatting.

const TYPO: Record<string, string> = { "„": '"', "“": '"', "”": '"', "‚": "'", "‘": "'", "’": "'", "–": "-", "—": "-", " ": " " };

/** Normalised text plus, for each normalised character, its index in the original. */
function normalise(text: string): { norm: string; map: number[] } {
  let norm = "";
  const map: number[] = [];
  let lastSpace = true;
  for (let i = 0; i < text.length; i++) {
    let ch = text[i].normalize("NFKC");
    ch = TYPO[ch] ?? ch;
    if (/\s/.test(ch)) {
      if (lastSpace) continue;
      ch = " ";
      lastSpace = true;
    } else {
      lastSpace = false;
    }
    for (const c of ch.toLowerCase()) {
      norm += c;
      map.push(i);
    }
  }
  return { norm, map };
}

export interface Range {
  start: number;
  end: number;
}

/** Character ranges of `quote` in `text`, or [] if it isn't there. */
export function findQuote(text: string, quote: string | null | undefined): Range[] {
  if (!quote) return [];
  const hay = normalise(text);
  const parts = normalise(quote)
    .norm.split(/\.\.\.|…/)
    .map((p) => p.trim().replace(/^["'.]+|["'.]+$/g, ""))
    .filter(Boolean);
  if (!parts.length) return [];
  const ranges: Range[] = [];
  let pos = 0;
  for (const part of parts) {
    const idx = hay.norm.indexOf(part, pos);
    if (idx === -1) return [];
    ranges.push({ start: hay.map[idx], end: hay.map[idx + part.length - 1] + 1 });
    pos = idx + part.length;
  }
  return ranges;
}

export const PLACEHOLDER = /\[[A-Z_]+_\d+\]/g;
export const BLANK = /\[[^\]\n]{2,80}\]/g;

/** Editorial blanks like [Begründung ergänzen] — not masked values such as [IBAN_1]. */
export function blanks(text: string): string[] {
  return [...new Set([...text.matchAll(BLANK)].map((m) => m[0]).filter((b) => !/^\[[A-Z_]+_\d+\]$/.test(b)))];
}

export const placeholderLabel = (ph: string): string =>
  ({ IBAN: "IBAN", STEUERNUMMER: "tax number", STEUER_ID: "tax ID", RV_NUMMER: "pension no.", KV_NUMMER: "insurance no.", PHONE: "phone", EMAIL: "e-mail", PERSONAL: "your data", NAME: "name", ADDRESS: "address", BIRTHDATE: "birthdate" })[
    ph.slice(1, -1).replace(/_\d+$/, "")
  ] ?? "masked";

// --------------------------------------------------------------------------- formatting

export const fmtDate = (iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = {}) =>
  iso ? new Date(`${iso}T12:00:00`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric", ...opts }) : "unknown";

export const eur = (n: number) => `${n.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;

export const pretty = (s: string) => s.replace(/_/g, " ");

export function daysLabel(days: number | null): string {
  if (days === null) return "date unknown";
  if (days < 0) return `${-days} day${days === -1 ? "" : "s"} ago`;
  if (days === 0) return "today";
  return days === 1 ? "tomorrow" : `in ${days} days`;
}

/** An .ics calendar file with one all-day event per deadline, with a reminder 3 days before. */
export function icsFor(items: { title: string; date: string; description: string }[]): string {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
  const esc = (s: string) => s.replace(/[\\;,]/g, (c) => `\\${c}`).replace(/\n/g, "\\n");
  const events = items.map((it, i) => {
    const d = it.date.replace(/-/g, "");
    const next = new Date(`${it.date}T12:00:00`);
    next.setDate(next.getDate() + 1);
    const end = next.toISOString().slice(0, 10).replace(/-/g, "");
    return [
      "BEGIN:VEVENT",
      `UID:briefkasten-${d}-${i}@local`,
      `DTSTAMP:${stamp}`,
      `DTSTART;VALUE=DATE:${d}`,
      `DTEND;VALUE=DATE:${end}`,
      `SUMMARY:${esc(it.title)}`,
      `DESCRIPTION:${esc(it.description)}`,
      "BEGIN:VALARM",
      "TRIGGER:-P3D",
      "ACTION:DISPLAY",
      `DESCRIPTION:${esc(it.title)}`,
      "END:VALARM",
      "END:VEVENT",
    ].join("\r\n");
  });
  return ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Briefkasten//EN", ...events, "END:VCALENDAR"].join("\r\n");
}

export function download(filename: string, content: string, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  URL.revokeObjectURL(url);
}
