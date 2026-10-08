"use client";

import { useEffect, useState, type ReactElement } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, ErrorNote, PageTitle } from "@/components/ui";
import { chartRows, lastDays, type ChartRow } from "@/lib/analytics";
import { api, errorMessage } from "@/lib/api-client";
import { formatMs, formatPercent, formatUsd } from "@/lib/format";
import type { UsageReport } from "@/lib/types";

const RANGES = [7, 30, 90] as const;
const COLORS = {
  llm: "#4f46e5",
  cache: "#10b981",
  p50: "#0ea5e9",
  p95: "#f97316",
  cost: "#8b5cf6",
};
// Days without questions have no value, so a line can be a single point: show points.
const DOT = { r: 3 };

export function AnalyticsView() {
  const [range, setRange] = useState<(typeof RANGES)[number]>(30);
  const [report, setReport] = useState<UsageReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const { from, to } = lastDays(range, new Date());
    api<UsageReport>(`/analytics/usage?from=${from}&to=${to}`).then(
      (data) => {
        if (!active) return;
        setReport(data);
        setError(null);
      },
      (err: unknown) => active && setError(errorMessage(err)),
    );
    return () => {
      active = false;
    };
  }, [range]);

  const rows = report ? chartRows(report.days) : [];
  const totals = report?.totals;

  return (
    <>
      <PageTitle
        title="Analytics"
        description="Questions, cache hits, answer times and cost, per day (UTC). Cost uses the LLM's price per token; answers from the cache cost nothing."
      />
      <div className="mb-6 flex gap-1" role="group" aria-label="Time range">
        {RANGES.map((days) => (
          <button
            key={days}
            type="button"
            aria-pressed={range === days}
            onClick={() => setRange(days)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
              range === days
                ? "bg-indigo-600 text-white"
                : "bg-white text-gray-700 hover:bg-gray-100"
            }`}
          >
            Last {days} days
          </button>
        ))}
      </div>
      <ErrorNote message={error} />
      {totals && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <Stat label="Questions" value={totals.questions.toLocaleString("en")} />
            <Stat label="From the cache" value={formatPercent(totals.cache_hit_rate)} />
            <Stat label="Cost" value={formatUsd(totals.cost_usd)} />
            <Stat label="Answer time p50" value={formatMs(totals.latency_p50_ms)} />
            <Stat label="Answer time p95" value={formatMs(totals.latency_p95_ms)} />
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <Chart title="Questions per day">
              <BarChart data={rows}>
                <Axes />
                <Bar dataKey="fromLlm" name="Answered by the LLM" stackId="q" fill={COLORS.llm} />
                <Bar dataKey="fromCache" name="From the cache" stackId="q" fill={COLORS.cache} />
              </BarChart>
            </Chart>
            <Chart title="Answer time">
              <LineChart data={rows}>
                <Axes format={(value) => formatMs(value)} />
                <Line dataKey="p50" name="p50" stroke={COLORS.p50} dot={DOT} />
                <Line dataKey="p95" name="p95" stroke={COLORS.p95} dot={DOT} />
              </LineChart>
            </Chart>
            <Chart title="Cache hit rate">
              <LineChart data={rows}>
                <Axes domain={[0, 100]} format={(value) => `${value}%`} />
                <Line dataKey="hitRate" name="From the cache" stroke={COLORS.cache} dot={DOT} />
              </LineChart>
            </Chart>
            <Chart title="Cost per day">
              <BarChart data={rows}>
                <Axes format={(value) => formatUsd(value)} />
                <Bar dataKey="cost" name="Cost" fill={COLORS.cost} />
              </BarChart>
            </Chart>
          </div>
        </div>
      )}
      {!report && !error && <p className="text-sm text-gray-500">Loading...</p>}
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
      <p className="text-xs font-medium text-gray-500 uppercase">{label}</p>
      <p className="mt-1 text-xl font-semibold text-gray-900">{value}</p>
    </div>
  );
}

function Chart({ title, children }: { title: string; children: ReactElement }) {
  return (
    <Card title={title}>
      <div className="h-60">
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

/** The grid, the axes, the tooltip and the legend that every chart shares. */
function Axes({
  domain,
  format,
}: {
  domain?: [number, number];
  format?: (value: number) => string;
}) {
  const show = (value: unknown) =>
    typeof value === "number" && format ? format(value) : String(value);
  return (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
      <XAxis dataKey={"label" satisfies keyof ChartRow} tick={{ fontSize: 12 }} minTickGap={16} />
      <YAxis domain={domain} tick={{ fontSize: 12 }} tickFormatter={show} width={64} />
      <Tooltip formatter={show} />
      <Legend wrapperStyle={{ fontSize: 12 }} />
    </>
  );
}
