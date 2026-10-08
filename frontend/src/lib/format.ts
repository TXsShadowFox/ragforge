/** Numbers and dates for people. */

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "850 ms", "1.3 s", or "–" when there is no value. */
export function formatMs(ms: number | null): string {
  if (ms === null) return "–";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

/** Dollars. Answers cost millionths of a dollar, so small amounts keep 6 decimals. */
export function formatUsd(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(6).replace(/0+$/, "")}`;
  return `$${value.toFixed(2)}`;
}

/** 0.9 -> "90%", 0.1234 -> "12.3%". */
export function formatPercent(rate: number): string {
  const percent = Math.round(rate * 1000) / 10;
  return `${percent}%`;
}

/** An ISO date-time, like "Oct 8, 2026, 7:16 AM", in the viewer's time zone. */
export function formatDateTime(iso: string, timeZone?: string): string {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone,
  }).format(new Date(iso));
}

const FILE_TYPES: Record<string, string> = {
  "application/pdf": "PDF",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
  "text/plain": "Text",
  "text/markdown": "Markdown",
  "text/html": "HTML",
};

export function fileType(mimeType: string): string {
  return FILE_TYPES[mimeType] ?? mimeType;
}
