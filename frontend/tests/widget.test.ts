// @vitest-environment happy-dom
/** The chat widget (../widget/widget.js) in a simulated browser, with a fake API. */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// npm runs the tests in frontend/. (In a browser-like test, import.meta.url is not a file.)
const WIDGET_SOURCE = readFileSync(join(process.cwd(), "..", "widget", "widget.js"), "utf8");
const API = "https://api.example";
const PUBLIC_KEY = "rf_pub_widgettestkey0123456789";

const ANSWER = {
  message_id: "m-1",
  session_id: "s-1",
  answer: "The late fee is 5 rupees per day. [1]",
  citations: [
    {
      number: 1,
      document_id: "d-1",
      filename: "rules.pdf",
      page: 4,
      snippet: "A late fee of 5 rupees per day applies.",
      chunk_id: "c-1",
    },
  ],
  usage: { prompt_tokens: 247, completion_tokens: 50 },
  cache_hit: false,
  latency_ms: 900,
};

const fetchMock = vi.fn<typeof fetch>();
const page = window as unknown as { RAGForgeWidget?: { open: () => void } };

beforeEach(() => {
  document.body.replaceChildren();
  sessionStorage.clear();
  delete page.RAGForgeWidget;
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Run widget.js as if a page had `<script src=".../widget.js" data-...>`. */
function loadWidget(attributes: Record<string, string> = {}): void {
  const script = document.createElement("script");
  script.src = `${API}/widget.js`;
  for (const [name, value] of Object.entries({ "data-api-key": PUBLIC_KEY, ...attributes })) {
    script.setAttribute(name, value);
  }
  Object.defineProperty(document, "currentScript", { value: script, configurable: true });
  try {
    new Function(WIDGET_SOURCE)();
  } finally {
    Object.defineProperty(document, "currentScript", { value: null, configurable: true });
  }
}

function widget(): ShadowRoot {
  const root = document.getElementById("ragforge-widget")?.shadowRoot;
  if (!root) throw new Error("The widget is not on the page");
  return root;
}

function find<T extends Element = HTMLElement>(selector: string): T {
  const node = widget().querySelector<T>(selector);
  if (!node) throw new Error(`No ${selector} in the widget`);
  return node;
}

function texts(selector: string): string[] {
  return Array.from(widget().querySelectorAll(selector), (node) => node.textContent ?? "");
}

function sse(events: Array<[string, unknown]>): Response {
  const body = events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);
  return new Response(body.join(""), { headers: { "content-type": "text/event-stream" } });
}

function apiError(status: number, code: string, headers: Record<string, string> = {}): Response {
  return Response.json({ error: { code, message: code } }, { status, headers });
}

const STREAMED_ANSWER: Array<[string, unknown]> = [
  ["start", { session_id: "s-1", message_id: "m-1" }],
  ["token", { text: "The late fee " }],
  ["token", { text: "is 5 rupees per day. [1]" }],
  ["done", ANSWER],
];

/** Type a question, send it, and wait until the answer is complete. */
async function ask(question: string): Promise<void> {
  find<HTMLTextAreaElement>(".input").value = question;
  find<HTMLFormElement>(".form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  );
  await vi.waitFor(() => expect(find<HTMLButtonElement>(".send").disabled).toBe(false));
}

function sentBody(call: number): Record<string, unknown> {
  return JSON.parse(String(fetchMock.mock.calls[call][1]?.body));
}

describe("the chat widget", () => {
  it("adds a chat bubble that opens the chat", () => {
    loadWidget({ "data-title": "Ask Acme College" });

    const panel = find(".panel");
    expect(panel.hidden).toBe(true);
    find(".launcher").click();
    expect(panel.hidden).toBe(false);
    expect(find(".title").textContent).toBe("Ask Acme College");
    expect(find(".launcher").getAttribute("aria-expanded")).toBe("true");
  });

  it("streams an answer with its sources", async () => {
    fetchMock.mockResolvedValue(sse(STREAMED_ANSWER));
    loadWidget();

    await ask("What is the late fee?");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API}/v1/chat`);
    expect(new Headers(init?.headers).get("authorization")).toBe(`Bearer ${PUBLIC_KEY}`);
    expect(init?.credentials).toBe("omit");
    expect(sentBody(0)).toEqual({
      question: "What is the late fee?",
      session_id: null,
      stream: true,
    });
    expect(texts(".user")).toEqual(["What is the late fee?"]);
    expect(texts(".bot .text")).toEqual([ANSWER.answer]);
    expect(texts(".sources li")).toEqual(["[1] rules.pdf, page 4"]);
  });

  it("sends follow-up questions in the same conversation", async () => {
    fetchMock.mockImplementation(async () => sse(STREAMED_ANSWER));
    loadWidget();

    await ask("What is the late fee?");
    await ask("And for magazines?");

    expect(sentBody(1).session_id).toBe("s-1");
  });

  it("starts a new conversation when the server no longer knows the old one", async () => {
    fetchMock.mockResolvedValueOnce(sse(STREAMED_ANSWER));
    fetchMock.mockResolvedValueOnce(apiError(404, "not_found"));
    fetchMock.mockResolvedValueOnce(sse(STREAMED_ANSWER));
    loadWidget();

    await ask("What is the late fee?");
    await ask("And for magazines?");

    expect(sentBody(1).session_id).toBe("s-1");
    expect(sentBody(2).session_id).toBeNull(); // asked again, in a new conversation
    expect(texts(".error")).toEqual([]);
  });

  it("does not keep a conversation whose first answer failed", async () => {
    fetchMock.mockResolvedValueOnce(
      sse([
        ["start", { session_id: "never-saved", message_id: "m-0" }],
        ["error", { code: "llm_busy", message: "busy" }],
      ]),
    );
    fetchMock.mockResolvedValueOnce(sse(STREAMED_ANSWER));
    loadWidget();

    await ask("What is the late fee?");
    await ask("What is the late fee?");

    expect(texts(".error")).toEqual([
      "The assistant is busy right now. Please try again in a moment.",
    ]);
    expect(sentBody(1).session_id).toBeNull();
  });

  it("says when to try again after too many questions", async () => {
    fetchMock.mockResolvedValue(apiError(429, "rate_limited", { "Retry-After": "30" }));
    loadWidget();

    await ask("Hello?");

    expect(texts(".error")).toEqual(["Too many questions. Please try again in 30 seconds."]);
  });

  it("tells the website owner when the website is not allowed", async () => {
    fetchMock.mockResolvedValue(apiError(403, "origin_not_allowed"));
    loadWidget();

    await ask("Hello?");

    expect(texts(".error")).toEqual(["This website is not allowed to use this chat."]);
    expect(console.error).toHaveBeenCalledWith(expect.stringContaining("allowed origins"));
  });

  it("shows a network problem as a message", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    loadWidget();

    await ask("Hello?");

    expect(texts(".error")[0]).toContain("Could not reach the assistant");
  });

  it("shows answers as text, never as HTML", async () => {
    const attack = '<img src="x" onerror="alert(1)">';
    fetchMock.mockResolvedValue(sse([["done", { ...ANSWER, answer: attack, citations: [] }]]));
    loadWidget();

    await ask("Hello?");

    expect(texts(".bot .text")).toEqual([attack]);
    expect(widget().querySelector("img")).toBeNull();
  });

  it("sends a rating for an answer", async () => {
    fetchMock.mockResolvedValueOnce(sse(STREAMED_ANSWER));
    fetchMock.mockResolvedValueOnce(Response.json({ rating: "up" }));
    loadWidget();
    await ask("What is the late fee?");

    find('.actions button[aria-label="Helpful"]').click();

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe(`${API}/v1/messages/m-1/feedback`);
    expect(JSON.parse(String(init?.body))).toEqual({ rating: "up" });
    expect(find('.actions button[aria-label="Helpful"]').getAttribute("aria-pressed")).toBe("true");
  });

  it("keeps the conversation when the visitor opens another page of the site", async () => {
    fetchMock.mockResolvedValue(sse(STREAMED_ANSWER));
    loadWidget();
    await ask("What is the late fee?");

    // A new page: the widget loads again and finds the conversation in sessionStorage.
    document.body.replaceChildren();
    delete page.RAGForgeWidget;
    loadWidget();

    expect(texts(".user")).toEqual(["What is the late fee?"]);
    expect(texts(".bot .text")).toEqual([ANSWER.answer]);
  });

  it("refuses a secret key, which must never be in a web page", () => {
    loadWidget({ "data-api-key": "rf_live_secret" });

    expect(document.getElementById("ragforge-widget")).toBeNull();
    expect(console.error).toHaveBeenCalledWith(expect.stringContaining("never put a secret key"));
  });

  it("can be opened from the website's own code", () => {
    loadWidget();

    page.RAGForgeWidget?.open();

    expect(find(".panel").hidden).toBe(false);
  });
});
