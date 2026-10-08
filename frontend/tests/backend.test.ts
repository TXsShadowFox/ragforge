/** The dashboard's server side: the /api/v1 proxy, the login cookie, and page redirects. */
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { forwardToApi, logIn } from "@/lib/backend";
import { config as proxyConfig, proxy } from "@/proxy";

const DASHBOARD = "http://localhost:3000";
const API = "http://api.test";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("API_URL", API);
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

type BrowserRequest = {
  method?: string;
  body?: string;
  cookie?: string | null; // null: not logged in
  origin?: string | null; // null: no Origin header
  headers?: Record<string, string>;
};

/** A request as a browser on the dashboard sends it. */
function browserRequest(path: string, options: BrowserRequest = {}): NextRequest {
  const method = options.method ?? "GET";
  const headers = new Headers({ host: "localhost:3000", ...options.headers });
  if (options.cookie !== null) headers.set("cookie", options.cookie ?? "rf_session=token-123");
  if (options.origin !== null && method !== "GET") {
    headers.set("origin", options.origin ?? DASHBOARD);
  }
  const init = { method, headers, body: options.body, duplex: options.body ? "half" : undefined };
  return new NextRequest(
    `${DASHBOARD}${path}`,
    init as ConstructorParameters<typeof NextRequest>[1],
  );
}

function lastCall(): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls.at(-1) ?? [];
  return { url: String(url), init: init ?? {} };
}

describe("the /api/v1 proxy", () => {
  it("adds the login token and keeps the path and the query", async () => {
    fetchMock.mockResolvedValue(Response.json({ items: [] }));

    const response = await forwardToApi(browserRequest("/api/v1/documents?limit=5"), ["documents"]);

    const { url, init } = lastCall();
    expect(url).toBe(`${API}/v1/documents?limit=5`);
    expect(new Headers(init.headers).get("authorization")).toBe("Bearer token-123");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ items: [] });
  });

  it("streams an upload's body to the API", async () => {
    fetchMock.mockResolvedValue(Response.json({ duplicate: false }, { status: 202 }));
    const request = browserRequest("/api/v1/documents", {
      method: "POST",
      body: "--boundary file bytes",
      headers: { "content-type": "multipart/form-data; boundary=boundary" },
    });

    const response = await forwardToApi(request, ["documents"]);

    const { init } = lastCall();
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("content-type")).toContain("multipart/form-data");
    expect(init.body).toBeInstanceOf(ReadableStream);
    expect(await new Response(init.body).text()).toBe("--boundary file bytes");
    expect(response.status).toBe(202);
  });

  it("passes rate limits on, with Retry-After", async () => {
    fetchMock.mockResolvedValue(
      Response.json(
        { error: { code: "rate_limited" } },
        { status: 429, headers: { "retry-after": "30", "x-ratelimit-limit": "10" } },
      ),
    );

    const response = await forwardToApi(browserRequest("/api/v1/me"), ["me"]);

    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("30");
    expect(response.headers.get("x-ratelimit-limit")).toBe("10");
  });

  it("lets streamed answers through without compression or buffering", async () => {
    fetchMock.mockResolvedValue(
      new Response("event: token\ndata: {}\n\n", {
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const response = await forwardToApi(browserRequest("/api/v1/chat", { method: "POST" }), [
      "chat",
    ]);

    expect(response.headers.get("cache-control")).toBe("no-cache, no-transform");
    expect(response.headers.get("x-accel-buffering")).toBe("no");
    expect(await response.text()).toBe("event: token\ndata: {}\n\n");
  });

  it("forgets the login when the API says the token is no longer valid", async () => {
    fetchMock.mockResolvedValue(Response.json({ error: {} }, { status: 401 }));

    const response = await forwardToApi(browserRequest("/api/v1/me"), ["me"]);

    expect(response.status).toBe(401);
    expect(response.headers.get("set-cookie")).toMatch(/^rf_session=; Path=\/; Max-Age=0/);
  });

  it("sends no body for 204 No Content", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    const response = await forwardToApi(
      browserRequest("/api/v1/api-keys/k1", { method: "DELETE" }),
      ["api-keys", "k1"],
    );

    expect(response.status).toBe(204);
    expect(response.body).toBeNull();
  });

  it("refuses requests without a login, without calling the API", async () => {
    const response = await forwardToApi(browserRequest("/api/v1/me", { cookie: null }), ["me"]);

    expect(response.status).toBe(401);
    expect((await response.json()).error.code).toBe("not_logged_in");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["another website", "http://evil.example"],
    ["another port of the same site", "http://localhost:5500"],
    ["no Origin header", null],
  ])("refuses a change sent from %s", async (_case, origin) => {
    const request = browserRequest("/api/v1/api-keys", { method: "POST", origin });

    const response = await forwardToApi(request, ["api-keys"]);

    expect(response.status).toBe(403);
    expect((await response.json()).error.code).toBe("cross_origin");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses paths that try to leave /v1", async () => {
    const response = await forwardToApi(browserRequest("/api/v1/x"), ["..", "metrics"]);

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers 502 when the API cannot be reached", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    const response = await forwardToApi(browserRequest("/api/v1/me"), ["me"]);

    expect(response.status).toBe(502);
    expect((await response.json()).error.code).toBe("api_unreachable");
  });
});

describe("logging in", () => {
  const credentials = { email: "owner@example.com", password: "correct horse battery" };

  it("keeps the token in an httpOnly cookie and never gives it to the page", async () => {
    fetchMock.mockResolvedValue(Response.json({ access_token: "jwt-secret", expires_in: 3600 }));

    const response = await logIn(browserRequest("/api/session", { method: "POST" }), credentials);

    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toMatch(/^rf_session=jwt-secret;/);
    expect(cookie).toMatch(/HttpOnly/i);
    expect(cookie).toMatch(/SameSite=lax/i);
    expect(cookie).toMatch(/Max-Age=3600/);
    expect(cookie).not.toMatch(/Secure/i); // plain HTTP on localhost
    expect(await response.text()).not.toContain("jwt-secret");
  });

  it("marks the cookie Secure behind an HTTPS proxy", async () => {
    fetchMock.mockResolvedValue(Response.json({ access_token: "jwt", expires_in: 3600 }));
    const request = browserRequest("/api/session", {
      method: "POST",
      headers: { "x-forwarded-proto": "https" },
    });

    const response = await logIn(request, credentials);

    expect(response.headers.get("set-cookie")).toMatch(/Secure/i);
  });

  it("passes a wrong password on as the API's 401", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ error: { code: "invalid_login" } }, { status: 401 }),
    );

    const response = await logIn(browserRequest("/api/session", { method: "POST" }), credentials);

    expect(response.status).toBe(401);
    expect((await response.json()).error.code).toBe("invalid_login");
  });
});

describe("page redirects (proxy.ts)", () => {
  function visit(path: string, loggedIn: boolean) {
    const headers = loggedIn ? { cookie: "rf_session=token" } : undefined;
    return proxy(new NextRequest(`${DASHBOARD}${path}`, { headers }));
  }

  it("sends visitors without a login to /login", () => {
    const response = visit("/documents", false);
    expect(response.headers.get("location")).toBe(`${DASHBOARD}/login`);
  });

  it("sends logged-in users from the login pages to their documents", () => {
    expect(visit("/login", true).headers.get("location")).toBe(`${DASHBOARD}/documents`);
    expect(visit("/", true).headers.get("location")).toBe(`${DASHBOARD}/documents`);
  });

  it("lets the right visitors through", () => {
    expect(visit("/documents", true).headers.get("location")).toBeNull();
    expect(visit("/signup", false).headers.get("location")).toBeNull();
  });

  it("never runs for /api: Next.js would cut request bodies (uploads) at 10 MB", () => {
    const pattern = new RegExp(`^${proxyConfig.matcher[0]}$`);
    expect(pattern.test("/api/v1/documents")).toBe(false);
    expect(pattern.test("/_next/static/app.js")).toBe(false);
    expect(pattern.test("/documents")).toBe(true);
  });
});
