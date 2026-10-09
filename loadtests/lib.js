/**
 * Shared parts of the load tests: a fresh tenant with documents to search, the questions,
 * the load steps, and a small results table (written to loadtests/results/).
 *
 * The scripts run in the k6 container of docker-compose.fake-llm.yml (`make loadtest`).
 */
import http from "k6/http";
import { check, fail, sleep } from "k6";

export const API = __ENV.API_URL || "http://api:8000";
const JSON_HEADERS = { "Content-Type": "application/json" };

// The evaluation's 5 sample documents (eval/corpus): 14 chunks.
const CORPUS = [
  ["student-handbook.pdf", "application/pdf", open("/corpus/student-handbook.pdf", "b")],
  ["library-guide.md", "text/markdown", open("/corpus/library-guide.md")],
  ["housing-rules.html", "text/html", open("/corpus/housing-rules.html")],
  ["it-help.txt", "text/plain", open("/corpus/it-help.txt")],
  ["campus-services.md", "text/markdown", open("/corpus/campus-services.md")],
];
// Generated documents: with them, each search finds the full 20 candidates (like in a real
// tenant), so the reranker always scores 20 chunks.
const GENERATED_DOCUMENTS = 4;

// Questions the documents answer (from eval/questions.json).
export const QUESTIONS = [
  "When does the spring semester start?",
  "How much is the tuition fee for the year?",
  "What minimum attendance do I need to take the final exam?",
  "What is the pass mark for a course?",
  "How many books can an undergraduate borrow at once?",
  "What is the fine for returning a library book late?",
  "Is the Science Library open on Sundays?",
  "What time do the residence hall gates close on Saturday?",
  "How much is the deposit when moving into a residence hall?",
  "What is the name of the Wi-Fi network for students?",
  "Do students have to pay to see the campus doctor?",
  "How much does a parking permit cost?",
];

export function pick(items) {
  return items[Math.floor(Math.random() * items.length)];
}

export function auth(key, extra = {}) {
  return { Authorization: `Bearer ${key}`, ...extra };
}

/** A new tenant with an API key; with its documents uploaded and ready (unless told not to). */
export function setUpTenant(name, { withDocuments = true } = {}) {
  const email = `load-${Date.now()}-${Math.floor(Math.random() * 1e9)}@example.com`;
  const password = "load-test-password-1";
  let res = http.post(
    `${API}/v1/auth/signup`,
    JSON.stringify({ tenant_name: name, email, password }),
    { headers: JSON_HEADERS },
  );
  expectStatus(res, [201], "sign up");
  res = http.post(`${API}/v1/auth/login`, JSON.stringify({ email, password }), {
    headers: JSON_HEADERS,
  });
  expectStatus(res, [200], "log in");
  res = http.post(`${API}/v1/api-keys`, JSON.stringify({ name: "load test" }), {
    headers: auth(res.json("access_token"), JSON_HEADERS),
  });
  expectStatus(res, [201], "create an API key");
  const key = res.json("key");
  if (withDocuments) {
    for (const [filename, type, data] of CORPUS) upload(key, filename, data, type);
    for (let number = 1; number <= GENERATED_DOCUMENTS; number++) {
      upload(key, `campus-clubs-${number}.txt`, generatedText(number, 100), "text/plain");
    }
    waitUntilReady(key, CORPUS.length + GENERATED_DOCUMENTS);
  }
  return { key };
}

export function upload(key, filename, data, type, tags = {}) {
  const res = http.post(
    `${API}/v1/documents`,
    { file: http.file(data, filename, type) },
    { headers: auth(key), tags },
  );
  expectStatus(res, [200, 202], `upload ${filename}`);
  return res;
}

/** Wait until `count` documents are ready (the worker parses, chunks and embeds them). */
export function waitUntilReady(key, count, timeoutSeconds = 300) {
  const deadline = Date.now() + timeoutSeconds * 1000;
  while (Date.now() < deadline) {
    if (countDocuments(key, "ready") >= count) return;
    sleep(1);
  }
  fail(`${count} documents were not ready after ${timeoutSeconds} s`);
}

export function countDocuments(key, status) {
  let total = 0;
  let cursor = null;
  do {
    const query = `limit=100&status=${status}${cursor ? `&cursor=${cursor}` : ""}`;
    const res = http.get(`${API}/v1/documents?${query}`, { headers: auth(key) });
    expectStatus(res, [200], "list the documents");
    total += res.json("items").length;
    cursor = res.json("next_cursor");
  } while (cursor);
  return total;
}

/** A made-up but realistic text (opening hours and prices), the same for the same seed. */
export function generatedText(seed, sentences) {
  const random = mulberry32(seed);
  const places = [
    "The career office", "The sports complex", "The language lab", "The art studio",
    "The music room", "The canteen", "The print shop", "The swimming pool",
    "The robotics club", "The debate society", "The photography club", "The counselling centre",
  ];
  const days = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"];
  const things = [
    "a locker", "a guest pass", "a training session", "a workshop seat", "an equipment loan",
    "a practice room", "a membership card", "a late-night pass",
  ];
  const lines = [];
  for (let number = 1; number <= sentences; number++) {
    const from = 6 + Math.floor(random() * 5);
    const to = 4 + Math.floor(random() * 6);
    const price = 10 * (1 + Math.floor(random() * 50));
    lines.push(
      `${pickWith(random, places)} opens at ${from} am and closes at ${to} pm on ` +
        `${pickWith(random, days)}, and ${pickWith(random, things)} costs ${price} rupees.`,
    );
    if (number % 5 === 0) lines.push("");
  }
  return lines.join("\n");
}

function pickWith(random, items) {
  return items[Math.floor(random() * items.length)];
}

function mulberry32(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function expectStatus(res, statuses, what) {
  if (!statuses.includes(res.status)) {
    fail(`${what}: HTTP ${res.status} ${String(res.body).slice(0, 300)}`);
  }
}

export function ask(key, question, { stream = false } = {}) {
  const res = http.post(`${API}/v1/chat`, JSON.stringify({ question, stream }), {
    headers: auth(key, JSON_HEADERS),
    tags: { name: "chat" },
    timeout: "120s",
  });
  check(res, { "status 200": (r) => r.status === 200 });
  return res;
}

// ------------------------------------------------------------------ load steps ----

const GRACEFUL_STOP_SECONDS = 5;

/** One step per number of users, one after the other, each `seconds` long. */
export function steps(levels, seconds) {
  const scenarios = {};
  levels.forEach((vus, index) => {
    scenarios[`users_${vus}`] = {
      executor: "constant-vus",
      vus,
      duration: `${seconds}s`,
      startTime: `${index * (seconds + GRACEFUL_STOP_SECONDS + 2)}s`,
      gracefulStop: `${GRACEFUL_STOP_SECONDS}s`,
    };
  });
  return scenarios;
}

/** Thresholds that never fail a step, but make k6 keep the numbers of each step apart. */
export function stepThresholds(scenarios) {
  const thresholds = { http_req_failed: ["rate<0.05"] };
  for (const name of Object.keys(scenarios)) {
    thresholds[`http_req_duration{scenario:${name}}`] = ["max>=0"];
    thresholds[`http_req_waiting{scenario:${name}}`] = ["max>=0"];
    thresholds[`http_reqs{scenario:${name}}`] = ["count>=0"];
    thresholds[`http_req_failed{scenario:${name}}`] = ["rate>=0"];
  }
  return thresholds;
}

export const TREND_STATS = ["avg", "min", "med", "p(95)", "p(99)", "max"];

/** handleSummary: a markdown table per step, plus all of k6's numbers as JSON. */
export function summaryWriter(name, title, scenarios, seconds, { firstByte = false } = {}) {
  return function handleSummary(data) {
    const header = firstByte
      ? "| Users | Requests | Req/s | p50 | p95 | p99 | First byte p50 / p95 | Errors |\n" +
        "|---:|---:|---:|---:|---:|---:|---:|---:|"
      : "| Users | Requests | Req/s | p50 | p95 | p99 | Errors |\n|---:|---:|---:|---:|---:|---:|---:|";
    const rows = Object.entries(scenarios).map(([scenario, config]) => {
      const values = (metric) => data.metrics[`${metric}{scenario:${scenario}}`]?.values ?? {};
      const duration = values("http_req_duration");
      const waiting = values("http_req_waiting");
      const count = values("http_reqs").count ?? 0;
      const cells = [
        config.vus,
        count,
        (count / seconds).toFixed(1),
        ms(duration.med),
        ms(duration["p(95)"]),
        ms(duration["p(99)"]),
      ];
      if (firstByte) cells.push(`${ms(waiting.med)} / ${ms(waiting["p(95)"])}`);
      cells.push(`${((values("http_req_failed").rate ?? 0) * 100).toFixed(1)}%`);
      return `| ${cells.join(" | ")} |`;
    });
    const extra = Object.entries(data.metrics)
      .filter(([metric]) => metric.startsWith("ingestion_"))
      .map(([metric, { values }]) => `- ${metric}: ${JSON.stringify(values)}`);
    const table = [`### ${title}`, "", header, ...rows, "", ...extra, ""].join("\n");
    return {
      [`/scripts/results/${name}.md`]: table,
      [`/scripts/results/${name}.json`]: JSON.stringify(data, null, 2),
      stdout: `\n${table}\n`,
    };
  };
}

function ms(value) {
  return value === undefined ? "-" : `${Math.round(value)} ms`;
}
