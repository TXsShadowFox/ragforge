/** Date ranges and chart rows for the analytics page. Days are UTC, like in the API. */

import type { DayUsage } from "@/lib/types";

export type ChartRow = {
  label: string; // "Oct 8"
  fromLlm: number;
  fromCache: number;
  hitRate: number | null; // percent; null on days without questions
  p50: number | null;
  p95: number | null;
  cost: number;
};

/** The last `count` days up to `today` (UTC), as the API's `from` and `to`. */
export function lastDays(count: number, today: Date): { from: string; to: string } {
  const end = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  const start = end - (count - 1) * 24 * 60 * 60 * 1000;
  return { from: isoDay(start), to: isoDay(end) };
}

export function chartRows(days: DayUsage[]): ChartRow[] {
  return days.map((day) => ({
    label: dayLabel(day.day),
    fromLlm: day.questions - day.cache_hits,
    fromCache: day.cache_hits,
    hitRate: day.questions > 0 ? Math.round((day.cache_hits / day.questions) * 1000) / 10 : null,
    p50: day.latency_p50_ms,
    p95: day.latency_p95_ms,
    cost: day.cost_usd,
  }));
}

function isoDay(utcMillis: number): string {
  return new Date(utcMillis).toISOString().slice(0, 10);
}

/** "2026-10-08" -> "Oct 8". */
function dayLabel(isoDate: string): string {
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(
    new Date(`${isoDate}T00:00:00Z`),
  );
}
