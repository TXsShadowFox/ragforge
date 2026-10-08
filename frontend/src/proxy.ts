/**
 * Before a page renders: without a login cookie, go to /login; logged in, skip the login
 * pages. Only a quick check: the API checks the token on every request.
 */
import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE } from "@/lib/session";

const LOGIN_PAGES = new Set(["/login", "/signup"]);

export function proxy(request: NextRequest) {
  const loggedIn = request.cookies.has(SESSION_COOKIE);
  const { pathname } = request.nextUrl;
  if (pathname === "/") return redirect(request, loggedIn ? "/documents" : "/login");
  if (!loggedIn && !LOGIN_PAGES.has(pathname)) return redirect(request, "/login");
  if (loggedIn && LOGIN_PAGES.has(pathname)) return redirect(request, "/documents");
  return NextResponse.next();
}

function redirect(request: NextRequest, path: string) {
  return NextResponse.redirect(new URL(path, request.url));
}

export const config = {
  // Pages only. Never /api: Next.js keeps the whole body of a request that passes through
  // here in memory, and cuts it at 10 MB, which would break uploads.
  matcher: ["/((?!api/|_next/|favicon.ico|.*\\.[a-zA-Z0-9]+$).*)"],
};
