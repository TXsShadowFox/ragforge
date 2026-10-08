import { describe, expect, it } from "vitest";

import { chartRows, lastDays, ratingRows } from "@/lib/analytics";
import { hasPending, mergeFirstPage } from "@/lib/documents";
import {
  fileType,
  formatBytes,
  formatDateTime,
  formatMs,
  formatPercent,
  formatUsd,
} from "@/lib/format";
import type { DayUsage, DocumentInfo } from "@/lib/types";

describe("formatting", () => {
  it("writes sizes in B, KB and MB", () => {
    expect([formatBytes(512), formatBytes(1536), formatBytes(3 * 1024 * 1024)]).toEqual([
      "512 B",
      "1.5 KB",
      "3.0 MB",
    ]);
  });

  it("writes times in ms below a second, then in seconds", () => {
    expect([formatMs(null), formatMs(18.4), formatMs(1296)]).toEqual(["–", "18 ms", "1.3 s"]);
  });

  it("keeps the small costs of answers readable", () => {
    expect([formatUsd(0), formatUsd(0.000034), formatUsd(0.0003), formatUsd(12.5)]).toEqual([
      "$0",
      "$0.000034",
      "$0.0003",
      "$12.50",
    ]);
  });

  it("writes rates as percents with one decimal at most", () => {
    expect([formatPercent(0.9), formatPercent(0.1234), formatPercent(0)]).toEqual([
      "90%",
      "12.3%",
      "0%",
    ]);
  });

  it("writes dates in the given time zone, and names file types", () => {
    // Newer Intl versions put a narrow no-break space before "AM": compare any space as " ".
    const written = formatDateTime("2026-10-08T02:16:35Z", "UTC").replace(/\s/g, " ");
    expect(written).toBe("Oct 8, 2026, 2:16 AM");
    expect(fileType("application/pdf")).toBe("PDF");
    expect(fileType("application/x-unknown")).toBe("application/x-unknown");
  });
});

describe("analytics", () => {
  it("gives the last N days up to today, in UTC", () => {
    // 01:30 on Oct 8 in UTC is still Oct 7 in New York: the range must use UTC days.
    const today = new Date("2026-10-08T01:30:00Z");
    expect(lastDays(7, today)).toEqual({ from: "2026-10-02", to: "2026-10-08" });
    expect(lastDays(1, today)).toEqual({ from: "2026-10-08", to: "2026-10-08" });
  });

  it("turns days into chart rows", () => {
    const days: DayUsage[] = [
      day("2026-10-07", { questions: 0, cache_hits: 0 }),
      day("2026-10-08", { questions: 10, cache_hits: 9, latency_p95_ms: 745.6, cost_usd: 3.4e-5 }),
    ];

    expect(chartRows(days)).toEqual([
      { label: "Oct 7", fromLlm: 0, fromCache: 0, hitRate: null, p50: null, p95: null, cost: 0 },
      {
        label: "Oct 8",
        fromLlm: 1,
        fromCache: 9,
        hitRate: 90,
        p50: null,
        p95: 745.6,
        cost: 3.4e-5,
      },
    ]);
  });
});

describe("ratings", () => {
  it("turns rated days into chart rows", () => {
    expect(ratingRows([{ day: "2026-10-08", up: 3, down: 1 }])).toEqual([
      { label: "Oct 8", up: 3, down: 1 },
    ]);
  });
});

describe("documents", () => {
  const ids = ["0199a000-0000-7000-8000-00000000000a", "0199a000-0000-7000-8000-00000000000b"];
  const [older, newer] = ids.map((id) => document(id, "ready"));

  it("checks again only while a document is still changing", () => {
    expect(hasPending([document(ids[0], "processing")])).toBe(true);
    expect(hasPending([document(ids[0], "deleting")])).toBe(true);
    expect(hasPending([older, document(ids[1], "failed")])).toBe(false);
  });

  it("replaces the list when everything fits on the first page", () => {
    const fresh = { ...newer, status: "ready" as const };
    expect(mergeFirstPage([newer, older], { items: [fresh], next_cursor: null })).toEqual([fresh]);
  });

  it("keeps older documents that came from 'Load more'", () => {
    expect(mergeFirstPage([newer, older], { items: [newer], next_cursor: newer.id })).toEqual([
      newer,
      older,
    ]);
  });
});

function day(isoDate: string, values: Partial<DayUsage>): DayUsage {
  return {
    day: isoDate,
    questions: 0,
    cache_hits: 0,
    tokens_in: 0,
    tokens_out: 0,
    cost_usd: 0,
    latency_p50_ms: null,
    latency_p95_ms: null,
    ...values,
  };
}

function document(id: string, status: DocumentInfo["status"]): DocumentInfo {
  return {
    id,
    filename: `${id}.pdf`,
    mime_type: "application/pdf",
    size_bytes: 100,
    status,
    error: null,
    chunk_count: 1,
    page_count: 1,
    created_at: "2026-10-08T00:00:00Z",
    updated_at: "2026-10-08T00:00:00Z",
  };
}
