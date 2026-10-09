/**
 * Uploads: each request sends a new small text file (~2 KB, 1 chunk). The API stores it,
 * saves the document and its job (outbox), and answers 202 at once; the worker processes
 * the files in the background. teardown() measures how long the worker needs after the
 * last upload (ingestion_wait_seconds).
 */
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";
import {
  TREND_STATS,
  countDocuments,
  generatedText,
  setUpTenant,
  stepThresholds,
  steps,
  summaryWriter,
  upload,
} from "./lib.js";

const SECONDS = Number(__ENV.SECONDS || 30);
const scenarios = steps((__ENV.USERS || "5,20").split(",").map(Number), SECONDS);
const ingestionWait = new Trend("ingestion_wait_seconds");
const ingestedDocuments = new Counter("ingestion_documents_ready");

export const options = {
  scenarios,
  thresholds: stepThresholds(scenarios),
  summaryTrendStats: TREND_STATS,
  setupTimeout: "120s",
  teardownTimeout: "900s",
};

export function setup() {
  return setUpTenant("Load test: uploads", { withDocuments: false });
}

export default function ({ key }) {
  // A new text (and so a new SHA-256) every time: never a duplicate.
  const text = `Upload ${__VU}-${__ITER}-${Date.now()}.\n${generatedText(__VU * 100000 + __ITER, 15)}`;
  const res = upload(key, `note-${__VU}-${__ITER}.txt`, text, "text/plain", { name: "upload" });
  check(res, { "accepted": (r) => r.status === 202 });
}

export function teardown({ key }) {
  const started = Date.now();
  while (countDocuments(key, "uploaded") + countDocuments(key, "processing") > 0) {
    if (Date.now() - started > 850 * 1000) break;
    sleep(1);
  }
  ingestionWait.add((Date.now() - started) / 1000);
  ingestedDocuments.add(countDocuments(key, "ready"));
}

export const handleSummary = summaryWriter("upload", "Uploads", scenarios, SECONDS);
