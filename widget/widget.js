// @ts-check
/**
 * RAGForge chat widget: one tag adds a chat bubble to any website.
 *
 *   <script src="https://YOUR-API/widget.js" data-api-key="rf_pub_..." async></script>
 *
 * Optional attributes: data-title, data-greeting, data-color (a CSS color) and
 * data-api-url (default: where this file was loaded from).
 *
 * Plain JavaScript: no libraries and no build step. Everything lives in a Shadow DOM, so
 * the page's CSS cannot change the widget and the widget cannot change the page.
 * Answers are always shown as plain text, never as HTML.
 */
(() => {
  "use strict";

  const VERSION = "1.0.0";
  const MAX_SAVED_MESSAGES = 40;
  const DEFAULT_TITLE = "Ask a question";
  const DEFAULT_GREETING = "Hi! Ask me anything about our documents.";
  const DEFAULT_COLOR = "#4f46e5";
  const NETWORK_ERROR = "Could not reach the assistant. Check your connection and try again.";
  const GENERIC_ERROR = "Something went wrong. Please try again.";

  /** @typedef {{number: number, filename: string, page: number | null, snippet: string}} Citation */
  /**
   * @typedef {object} SavedMessage
   * @property {"user" | "assistant" | "error"} role
   * @property {string} text
   * @property {Citation[]} [citations]
   * @property {string} [messageId]
   * @property {"up" | "down"} [rating]
   */
  /** @typedef {{sessionId: string | null, messages: SavedMessage[]}} ChatState */
  /** @typedef {{version: string, open: () => void, close: () => void, toggle: () => void}} WidgetApi */

  const globals = /** @type {Window & {RAGForgeWidget?: WidgetApi}} */ (window);
  const script = /** @type {HTMLScriptElement | null} */ (document.currentScript);
  if (!script) {
    console.error("RAGForge widget: load it with a <script src> tag.");
    return;
  }
  if (globals.RAGForgeWidget) {
    return; // already on this page
  }

  const apiKey = (script.dataset.apiKey || "").trim();
  if (apiKey.startsWith("rf_live_")) {
    console.error(
      "RAGForge widget: never put a secret key (rf_live_) in a web page. " +
        "Create a public key (rf_pub_) for this website in the dashboard.",
    );
    return;
  }
  if (!apiKey.startsWith("rf_pub_")) {
    console.error("RAGForge widget: set data-api-key to a public key (rf_pub_...).");
    return;
  }
  const apiUrl = (script.dataset.apiUrl || originOf(script.src)).replace(/\/+$/, "");
  if (!apiUrl) {
    console.error("RAGForge widget: set data-api-url to the address of the RAGForge API.");
    return;
  }
  const title = script.dataset.title || DEFAULT_TITLE;
  const greeting = script.dataset.greeting || DEFAULT_GREETING;
  const color = isColor(script.dataset.color) ? String(script.dataset.color) : DEFAULT_COLOR;
  const storageKey = `ragforge-widget:${apiKey.slice(0, 16)}`;

  /** An error with a message for the visitor. */
  class WidgetError extends Error {}

  /** @type {ChatState} */
  let state = loadState();
  let busy = false;

  function mount() {
    const host = document.createElement("div");
    host.id = "ragforge-widget";
    host.style.setProperty("--rf-color", color);
    const root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = STYLES;
    const view = document.createElement("div");
    view.innerHTML = MARKUP; // fixed markup: no data from the page or the API
    root.append(style, ...view.childNodes);
    document.body.append(host);

    const find = (/** @type {string} */ selector) => {
      const node = root.querySelector(selector);
      if (!node) throw new Error(`RAGForge widget: ${selector} is missing`);
      return /** @type {HTMLElement} */ (node);
    };
    const launcher = find(".launcher");
    const panel = find(".panel");
    const messages = find(".messages");
    const form = /** @type {HTMLFormElement} */ (find(".form"));
    const input = /** @type {HTMLTextAreaElement} */ (find(".input"));
    const sendButton = /** @type {HTMLButtonElement} */ (find(".send"));
    find(".title").textContent = title;
    panel.setAttribute("aria-label", title);

    const open = () => {
      panel.hidden = false;
      launcher.setAttribute("aria-expanded", "true");
      launcher.setAttribute("aria-label", "Close chat");
      scrollDown();
      input.focus();
    };
    const close = () => {
      panel.hidden = true;
      launcher.setAttribute("aria-expanded", "false");
      launcher.setAttribute("aria-label", "Open chat");
      launcher.focus();
    };
    const toggle = () => (panel.hidden ? open() : close());

    launcher.addEventListener("click", toggle);
    find(".close").addEventListener("click", close);
    find(".new").addEventListener("click", () => {
      state = { sessionId: null, messages: [] };
      saveState();
      render();
      input.focus();
    });
    panel.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close();
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void send();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void send();
      }
    });

    render();
    globals.RAGForgeWidget = { version: VERSION, open, close, toggle };

    function render() {
      messages.replaceChildren(textBlock("div", "msg bot", greeting));
      for (const message of state.messages) messages.append(renderMessage(message));
      scrollDown();
    }

    function scrollDown() {
      messages.scrollTop = messages.scrollHeight;
    }

    /** @param {SavedMessage} message */
    function addMessage(message) {
      state.messages.push(message);
      saveState();
      messages.append(renderMessage(message));
      scrollDown();
    }

    /** @param {SavedMessage} message */
    function renderMessage(message) {
      if (message.role === "user") return textBlock("div", "msg user", message.text);
      if (message.role === "error") return textBlock("div", "msg error", message.text);
      const bubble = textBlock("div", "msg bot", "");
      bubble.append(textBlock("div", "text", message.text));
      if (message.citations && message.citations.length > 0) {
        bubble.append(renderSources(message.citations));
      }
      if (message.messageId) bubble.append(renderRating(message));
      return bubble;
    }

    /** @param {SavedMessage} message */
    function renderRating(message) {
      const actions = textBlock("div", "actions", "");
      /** @type {Array<["up" | "down", string, string]>} */
      const choices = [
        ["up", "Helpful", "\u{1F44D}"],
        ["down", "Not helpful", "\u{1F44E}"],
      ];
      for (const [rating, label, icon] of choices) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = icon;
        button.setAttribute("aria-label", label);
        button.setAttribute("aria-pressed", String(message.rating === rating));
        button.addEventListener("click", () => {
          message.rating = rating;
          saveState();
          actions.replaceWith(renderRating(message));
          void sendRating(message, rating);
        });
        actions.append(button);
      }
      return actions;
    }

    async function send() {
      const question = input.value.trim();
      if (!question || busy) return;
      busy = true;
      sendButton.disabled = true;
      input.value = "";
      addMessage({ role: "user", text: question });

      /** @type {SavedMessage} */
      const answer = { role: "assistant", text: "" };
      const bubble = textBlock("div", "msg bot", "");
      const text = textBlock("div", "text typing", "");
      bubble.append(text);
      messages.append(bubble);
      scrollDown();
      try {
        let response = await askApi(question);
        if (response.status === 404 && state.sessionId) {
          state.sessionId = null; // the conversation is gone on the server: start a new one
          response = await askApi(question);
        }
        if (!response.ok || !response.body) throw await requestError(response);
        for await (const [name, data] of readEvents(response.body)) {
          if (name === "token") {
            answer.text += data.text;
            text.classList.remove("typing");
            text.textContent = answer.text;
            scrollDown();
          } else if (name === "done") {
            answer.text = data.answer;
            answer.citations = data.citations;
            answer.messageId = data.message_id;
            state.sessionId = data.session_id; // saved on the server only now
          } else if (name === "error") {
            throw new WidgetError(errorText(data.code, 0));
          }
        }
        if (!answer.messageId) throw new WidgetError(GENERIC_ERROR); // the stream broke off
        bubble.remove();
        addMessage(answer);
      } catch (error) {
        bubble.remove();
        if (answer.text) addMessage(answer); // keep what already arrived
        addMessage({
          role: "error",
          text: error instanceof WidgetError ? error.message : NETWORK_ERROR,
        });
      } finally {
        busy = false;
        sendButton.disabled = false;
        input.focus();
      }
    }
  }

  /** @param {string} question */
  function askApi(question) {
    return fetch(`${apiUrl}/v1/chat`, {
      method: "POST",
      headers: { ...jsonHeaders(), Accept: "text/event-stream" },
      body: JSON.stringify({
        question,
        session_id: state.sessionId,
        stream: true,
      }),
      credentials: "omit",
    });
  }

  /** @param {Citation[]} citations */
  function renderSources(citations) {
    const list = textBlock("ul", "sources", "");
    list.setAttribute("aria-label", "Sources");
    for (const citation of citations) {
      const where = citation.page
        ? `${citation.filename}, page ${citation.page}`
        : citation.filename;
      const item = textBlock("li", "", `[${citation.number}] ${where}`);
      item.title = citation.snippet;
      list.append(item);
    }
    return list;
  }

  /**
   * @param {SavedMessage} message
   * @param {"up" | "down"} rating
   */
  async function sendRating(message, rating) {
    try {
      const id = encodeURIComponent(String(message.messageId));
      const response = await fetch(`${apiUrl}/v1/messages/${id}/feedback`, {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ rating }),
        credentials: "omit",
      });
      if (!response.ok) console.warn("RAGForge widget: the rating was not saved", response.status);
    } catch (error) {
      console.warn("RAGForge widget: the rating was not saved", error);
    }
  }

  function jsonHeaders() {
    return {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    };
  }

  /**
   * The Server-Sent Events of a response body, as [event name, data] pairs.
   * @param {ReadableStream<Uint8Array>} body
   * @returns {AsyncGenerator<[string, any], void, unknown>}
   */
  async function* readEvents(body) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");
      let end = buffer.indexOf("\n\n");
      while (end >= 0) {
        const event = parseEvent(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        if (event) yield event;
        end = buffer.indexOf("\n\n");
      }
      if (done) return;
    }
  }

  /**
   * One event: "event: <name>" and "data: <json>" lines. Comment lines start with ":".
   * @param {string} block
   * @returns {[string, any] | null}
   */
  function parseEvent(block) {
    let name = "message";
    const data = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") name = value;
      else if (field === "data") data.push(value);
    }
    if (data.length === 0) return null;
    try {
      return [name, JSON.parse(data.join("\n"))];
    } catch {
      return null;
    }
  }

  /** @param {Response} response */
  async function requestError(response) {
    let code = "";
    try {
      code = String((await response.json()).error.code);
    } catch {
      code = String(response.status);
    }
    if (code === "origin_not_allowed") {
      console.error(
        `RAGForge widget: add ${location.origin} to the allowed origins of this public key.`,
      );
    }
    return new WidgetError(errorText(code, Number(response.headers.get("Retry-After")) || 0));
  }

  /**
   * @param {string} code
   * @param {number} waitSeconds
   */
  function errorText(code, waitSeconds) {
    switch (code) {
      case "rate_limited":
        return waitSeconds
          ? `Too many questions. Please try again in ${waitSeconds} seconds.`
          : "Too many questions. Please wait a moment.";
      case "origin_not_allowed":
        return "This website is not allowed to use this chat.";
      case "invalid_api_key":
      case "401":
        return "This chat is not set up correctly.";
      case "llm_busy":
      case "llm_unavailable":
        return "The assistant is busy right now. Please try again in a moment.";
      default:
        return GENERIC_ERROR;
    }
  }

  /** @returns {ChatState} */
  function loadState() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey) || "null");
      if (saved && Array.isArray(saved.messages)) {
        return { sessionId: saved.sessionId || null, messages: saved.messages };
      }
    } catch {
      // storage is blocked or holds bad data: start a new chat
    }
    return { sessionId: null, messages: [] };
  }

  function saveState() {
    try {
      const messages = state.messages.slice(-MAX_SAVED_MESSAGES);
      sessionStorage.setItem(storageKey, JSON.stringify({ sessionId: state.sessionId, messages }));
    } catch {
      // private mode or a full storage: the chat still works, it is just not kept
    }
  }

  /**
   * An element with text. textContent never turns text into HTML.
   * @param {string} tag
   * @param {string} className
   * @param {string} text
   */
  function textBlock(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  /** @param {string} url */
  function originOf(url) {
    try {
      return new URL(url).origin;
    } catch {
      return "";
    }
  }

  /** @param {string | undefined} value */
  function isColor(value) {
    return Boolean(value) && typeof CSS !== "undefined" && CSS.supports("color", String(value));
  }

  const MARKUP = `
    <button class="launcher" type="button" aria-label="Open chat" aria-expanded="false">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 3h16a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H9l-5 4v-4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/></svg>
    </button>
    <section class="panel" role="dialog" hidden>
      <header class="header">
        <h2 class="title"></h2>
        <button class="new" type="button">New chat</button>
        <button class="close" type="button" aria-label="Close chat">&times;</button>
      </header>
      <div class="messages" role="log" aria-live="polite"></div>
      <form class="form">
        <textarea class="input" rows="1" maxlength="2000" placeholder="Type your question"
          aria-label="Your question"></textarea>
        <button class="send" type="submit" aria-label="Send">&#10148;</button>
      </form>
      <p class="footer">Powered by RAGForge. Answers can contain mistakes.</p>
    </section>`;

  const STYLES = `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
    .launcher { position: fixed; right: 20px; bottom: 20px; z-index: 2147483000; width: 56px;
      height: 56px; display: grid; place-items: center; border: 0; border-radius: 50%;
      background: var(--rf-color); color: #fff; cursor: pointer;
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25); }
    .launcher svg { width: 26px; height: 26px; fill: currentColor; }
    button:focus-visible, textarea:focus-visible { outline: 3px solid var(--rf-color); outline-offset: 2px; }
    .panel { position: fixed; right: 20px; bottom: 88px; z-index: 2147483000; width: 370px;
      max-width: calc(100vw - 40px); height: 540px; max-height: calc(100vh - 120px);
      display: flex; flex-direction: column; overflow: hidden; border-radius: 14px;
      background: #fff; color: #111827; font-size: 14px; line-height: 1.45;
      box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25); }
    .panel[hidden] { display: none; }
    .header { display: flex; align-items: center; gap: 6px; padding: 12px 14px;
      background: var(--rf-color); color: #fff; }
    .title { flex: 1; margin: 0; font-size: 15px; font-weight: 600; }
    .header button { padding: 4px 8px; border: 0; border-radius: 6px; background: transparent;
      color: inherit; font-size: 13px; cursor: pointer; }
    .header button:hover { background: rgba(255, 255, 255, 0.15); }
    .header .close { font-size: 20px; line-height: 1; }
    .messages { flex: 1; display: flex; flex-direction: column; gap: 10px; overflow-y: auto;
      padding: 14px; background: #f9fafb; }
    .msg { max-width: 85%; padding: 9px 12px; border-radius: 12px; white-space: pre-wrap;
      overflow-wrap: anywhere; }
    .user { align-self: flex-end; border-bottom-right-radius: 4px; background: var(--rf-color);
      color: #fff; }
    .bot { align-self: flex-start; border: 1px solid #e5e7eb; border-bottom-left-radius: 4px;
      background: #fff; }
    .error { align-self: center; border: 1px solid #fecaca; background: #fef2f2; color: #991b1b;
      font-size: 13px; }
    .typing::after { content: "..."; animation: rf-blink 1s steps(1) infinite; }
    .sources { display: flex; flex-wrap: wrap; gap: 4px; margin: 8px 0 0; padding: 0;
      list-style: none; }
    .sources li { padding: 2px 8px; border-radius: 999px; background: #eef2ff; color: #3730a3;
      font-size: 12px; }
    .actions { display: flex; gap: 4px; margin-top: 6px; }
    .actions button { padding: 2px 6px; border: 1px solid #e5e7eb; border-radius: 6px;
      background: #fff; font-size: 13px; cursor: pointer; }
    .actions button[aria-pressed="true"] { border-color: var(--rf-color); background: #eef2ff; }
    .form { display: flex; gap: 8px; padding: 10px; border-top: 1px solid #e5e7eb;
      background: #fff; }
    .input { flex: 1; max-height: 120px; padding: 8px 10px; border: 1px solid #d1d5db;
      border-radius: 8px; color: inherit; font-size: 14px; resize: none; }
    .send { padding: 0 14px; border: 0; border-radius: 8px; background: var(--rf-color);
      color: #fff; font-size: 16px; cursor: pointer; }
    .send:disabled { opacity: 0.6; cursor: not-allowed; }
    .footer { margin: 0; padding: 4px 10px 8px; background: #fff; color: #6b7280;
      font-size: 11px; text-align: center; }
    @media (max-width: 480px) {
      .panel { right: 0; bottom: 0; width: 100vw; max-width: 100vw; height: 100%;
        max-height: 100%; border-radius: 0; }
    }
    @media (prefers-reduced-motion: reduce) { .typing::after { animation: none; } }
    @keyframes rf-blink { 50% { opacity: 0; } }`;

  if (document.body) {
    mount();
  } else {
    document.addEventListener("DOMContentLoaded", mount, { once: true });
  }
})();
