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
import { chartRows, lastDays, ratingRows, type ChartRow } from "@/lib/analytics";
import { api, errorMessage } from "@/lib/api-client";
import { formatDateTime, formatMs, formatPercent, formatUsd } from "@/lib/format";
import type { QualityReport, UsageReport } from "@/lib/types";

const RANGES = [7, 30, 90] as const;
const COLORS = {
  llm: "#4f46e5",
  cache: "#10b981",
  p50: "#0ea5e9",
  p95: "#f97316",
  cost: "#8b5cf6",
  up: "#10b981",
  down: "#ef4444",
};
// Days without questions have no value, so a line can be a single point: show points.
const DOT = { r: 3 };

export function AnalyticsView() {
  const [range, setRange] = useState<(typeof RANGES)[number]>(30);
  const [report, setReport] = useState<UsageReport | null>(null);
  const [quality, setQuality] = useState<QualityReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const { from, to } = lastDays(range, new Date());
    const days = `from=${from}&to=${to}`;
    Promise.all([
      api<UsageReport>(`/analytics/usage?${days}`),
      api<QualityReport>(`/analytics/quality?${days}`),
    ]).then(
      ([usage, ratings]) => {
        if (!active) return;
        setReport(usage);
        setQuality(ratings);
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
          {quality && <AnswerQuality quality={quality} />}
        </div>
      )}
      {!report && !error && <p className="text-sm text-gray-500">Loading...</p>}
    </>
  );
}

/** What users think of the answers: their thumbs up and down (playground and widget). */
function AnswerQuality({ quality }: { quality: QualityReport }) {
  const { totals } = quality;
  return (
    <section className="space-y-4" aria-labelledby="answer-quality">
      <div>
        <h2 id="answer-quality" className="text-lg font-semibold text-gray-900">
          Answer quality
        </h2>
        <p className="text-sm text-gray-600">
          Thumbs up and down from the playground and the chat widget.
        </p>
      </div>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat
          label="Good ratings"
          value={totals.satisfaction_rate === null ? "–" : formatPercent(totals.satisfaction_rate)}
        />
        <Stat label="Thumbs up" value={totals.up.toLocaleString("en")} />
        <Stat label="Thumbs down" value={totals.down.toLocaleString("en")} />
        <Stat label="Answers rated" value={`${totals.rated} of ${totals.answers}`} />
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Chart title="Ratings per day">
          <BarChart data={ratingRows(quality.days)}>
            <Axes />
            <Bar dataKey="up" name="Thumbs up" stackId="r" fill={COLORS.up} />
            <Bar dataKey="down" name="Thumbs down" stackId="r" fill={COLORS.down} />
          </BarChart>
        </Chart>
        <Card title="Latest thumbs down">
          {quality.recent_negative.length === 0 ? (
            <p className="text-sm text-gray-500">No thumbs down in these days.</p>
          ) : (
            <ul className="max-h-60 space-y-3 overflow-y-auto text-sm">
              {quality.recent_negative.map((item) => (
                <li key={item.message_id} className="border-b border-gray-100 pb-2">
                  <p className="font-medium text-gray-900">{item.question ?? "(question?)"}</p>
                  <p className="line-clamp-2 text-gray-600">{item.answer}</p>
                  {item.comment && (
                    <p className="text-gray-500 italic">&ldquo;{item.comment}&rdquo;</p>
                  )}
                  <p className="text-xs text-gray-400">{formatDateTime(item.rated_at)}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </section>
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
