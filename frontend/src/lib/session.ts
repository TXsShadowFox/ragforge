import type { NextRequest } from "next/server";

/** The httpOnly cookie that holds the API login token. Page scripts can never read it. */
export const SESSION_COOKIE = "rf_session";

/**
 * The login cookie: httpOnly (no JavaScript can read it), SameSite=Lax (other sites
 * cannot send it with their forms), and Secure when the dashboard runs on HTTPS.
 */
export function sessionCookie(request: NextRequest, token: string, maxAgeSeconds: number) {
  return {
    name: SESSION_COOKIE,
    value: token,
    httpOnly: true,
    sameSite: "lax" as const,
    secure: isHttps(request),
    path: "/",
    maxAge: maxAgeSeconds,
  };
}

/** HTTPS, also behind a proxy that ends HTTPS and forwards plain HTTP to us. */
export function isHttps(request: NextRequest): boolean {
  const forwarded = request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim();
  return (forwarded ?? request.nextUrl.protocol.replace(":", "")) === "https";
}

/**
 * A guard against cross-site request forgery: browsers send `Origin` with every POST,
 * PUT, PATCH and DELETE, so a change must come from a page of the dashboard itself.
 * SameSite=Lax alone is not enough: another port or subdomain is the "same site".
 */
export function isSameOrigin(request: NextRequest): boolean {
  if (request.method === "GET" || request.method === "HEAD") return true;
  const origin = request.headers.get("origin");
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}
