/**
 * Chat, answered from the cache: the same 12 questions again and again. Each was asked
 * once in setup(), so every answer comes from the exact cache (Redis), without search
 * or LLM. This is the fastest path of POST /v1/chat.
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
const scenarios = steps((__ENV.USERS || "10,50").split(",").map(Number), SECONDS);

export const options = {
  scenarios,
  thresholds: stepThresholds(scenarios),
  summaryTrendStats: TREND_STATS,
  setupTimeout: "600s",
};

export function setup() {
  const { key } = setUpTenant("Load test: cached chat");
  for (const question of QUESTIONS) ask(key, question); // fills the cache
  return { key };
}

export default function ({ key }) {
  const res = ask(key, pick(QUESTIONS));
  check(res, { "from the cache": (r) => r.status === 200 && r.json("cache_hit") === true });
}

export const handleSummary = summaryWriter(
  "chat_cached",
  "Chat, answers from the cache",
  scenarios,
  SECONDS,
);
