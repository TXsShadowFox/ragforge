/**
 * Chat, not cached: every question is new, so each one runs the whole RAG path: embed,
 * two searches, rerank 20 chunks, the (fake) LLM, save. The fake LLM takes ~0.5 s.
 *
 * STREAM=true asks for streamed answers (like the widget): then "first byte" is the time
 * until the answer starts (everything before the LLM).
 */
import { check } from "k6";
import {
  QUESTIONS,
  TREND_STATS,
  ask,
  pick,
  setUpTenant,
  stepThresholds,
  steps,
  summaryWriter,
} from "./lib.js";

const SECONDS = Number(__ENV.SECONDS || 30);
const STREAM = __ENV.STREAM === "true";
const scenarios = steps((__ENV.USERS || "1,5,10,20").split(",").map(Number), SECONDS);

export const options = {
  scenarios,
  thresholds: stepThresholds(scenarios),
  summaryTrendStats: TREND_STATS,
  setupTimeout: "600s",
};

export function setup() {
  return setUpTenant("Load test: chat");
}

export default function ({ key }) {
  // A new text every time: never in the cache (the semantic cache is off in this stack).
  const question = `${pick(QUESTIONS)} (load test ${__VU}-${__ITER})`;
  const res = ask(key, question, { stream: STREAM });
  check(res, {
    "not from the cache": (r) => r.status === 200 && (STREAM || r.json("cache_hit") === false),
  });
}

const name = STREAM ? "chat_uncached_stream" : "chat_uncached";
const title = STREAM ? "Chat, not cached, streamed" : "Chat, not cached";
export const handleSummary = summaryWriter(name, title, scenarios, SECONDS, { firstByte: true });
