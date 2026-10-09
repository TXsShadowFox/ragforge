/**
 * The dashboard's server talks to the RAGForge API for the browser. The browser only
 * talks to the dashboard: the login token stays in an httpOnly cookie, out of reach of
 * page scripts, and the dashboard needs no CORS.
 */
import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE, isSameOrigin, sessionCookie } from "@/lib/session";

/** API response headers that the browser may need. */
const PASSED_HEADERS = [
  "content-type",
  "retry-after",
  "x-ratelimit-limit",
  "x-ratelimit-remaining",
  "x-request-id",
];
const NO_BODY_STATUSES = new Set([101, 204, 205, 304]);

/** Where the API is, seen from this server (in Docker: http://api:8000). */
export function apiUrl(): string {
  return (process.env.API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

/**
 * The visitor's IP address, as the proxy in front (Caddy) reports it. The API needs it for
 * its limits per IP address (logins, sign-ups); without it, every dashboard user would
 * have this server's address. The API trusts it only behind that proxy.
 */
export function forwardedFor(request: NextRequest): Record<string, string> {
  const value = request.headers.get("x-forwarded-for");
  return value ? { "x-forwarded-for": value } : {};
}

/** An error in the API's format: {error: {code, message}}. */
export function errorResponse(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } }, { status });
}

/**
 * Send a browser request to the API's `/v1/<segments>`, with the login token from the
 * cookie. Bodies stream both ways, so uploads and answers are never held in memory.
 */
export async function forwardToApi(request: NextRequest, segments: string[]): Promise<Response> {
  if (segments.some((segment) => segment === "" || segment === "." || segment === "..")) {
    return errorResponse(400, "bad_path", "This path is not allowed.");
  }
  if (!isSameOrigin(request)) {
    return errorResponse(403, "cross_origin", "Requests must come from the dashboard itself.");
  }
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) {
    return errorResponse(401, "not_logged_in", "Please log in.");
  }
  const headers = new Headers({ authorization: `Bearer ${token}`, ...forwardedFor(request) });
  for (const name of ["content-type", "accept"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const path = segments.map(encodeURIComponent).join("/");
  // Node's fetch needs `duplex: "half"` to send a body that is a stream.
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    duplex: hasBody ? "half" : undefined,
    redirect: "manual",
    cache: "no-store",
    signal: request.signal, // the browser went away: stop the API call too
  };
  try {
    return relay(await fetch(`${apiUrl()}/v1/${path}${request.nextUrl.search}`, init));
  } catch {
    return errorResponse(502, "api_unreachable", "The RAGForge API cannot be reached.");
  }
}

/** Log in with the API and keep its token in the cookie. The token never reaches the page. */
export async function logIn(
  request: NextRequest,
  credentials: { email: string; password: string },
): Promise<Response> {
  let upstream: Response;
  try {
    upstream = await fetch(`${apiUrl()}/v1/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json", ...forwardedFor(request) },
      body: JSON.stringify(credentials),
      cache: "no-store",
    });
  } catch {
    return errorResponse(502, "api_unreachable", "The RAGForge API cannot be reached.");
  }
  if (!upstream.ok) return relay(upstream);
  const login = (await upstream.json()) as { access_token: string; expires_in: number };
  const response = NextResponse.json({ ok: true });
  response.cookies.set(sessionCookie(request, login.access_token, login.expires_in));
  return response;
}

/** Pass the API's response on: its status, its body (streamed) and some of its headers. */
export function relay(upstream: Response): Response {
  const headers = new Headers();
  for (const name of PASSED_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (headers.get("content-type")?.startsWith("text/event-stream")) {
    // No compression and no proxy buffering: every piece of an answer must go out at once.
    headers.set("cache-control", "no-cache, no-transform");
    headers.set("x-accel-buffering", "no");
  } else {
    headers.set("cache-control", "no-store");
  }
  if (upstream.status === 401) {
    // The token expired, or the user is gone: forget it, so the next page asks to log in.
    headers.append("set-cookie", `${SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax`);
  }
  const body = NO_BODY_STATUSES.has(upstream.status) ? null : upstream.body;
  return new Response(body, { status: upstream.status, headers });
}

/** The JSON body of a request, or null if it is not a JSON object. */
export async function readJsonObject(
  request: NextRequest,
): Promise<Record<string, unknown> | null> {
  try {
    const value: unknown = await request.json();
    return value !== null && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}
