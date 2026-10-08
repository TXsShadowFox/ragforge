/** Log in (POST {email, password}) and log out (DELETE). The token stays in the cookie. */
import { NextResponse, type NextRequest } from "next/server";

import { errorResponse, logIn, readJsonObject } from "@/lib/backend";
import { SESSION_COOKIE, isSameOrigin } from "@/lib/session";

export async function POST(request: NextRequest) {
  if (!isSameOrigin(request)) {
    return errorResponse(403, "cross_origin", "Requests must come from the dashboard itself.");
  }
  const body = await readJsonObject(request);
  if (typeof body?.email !== "string" || typeof body.password !== "string") {
    return errorResponse(400, "bad_request", "Send an email and a password.");
  }
  return logIn(request, { email: body.email, password: body.password });
}

export async function DELETE(request: NextRequest) {
  if (!isSameOrigin(request)) {
    return errorResponse(403, "cross_origin", "Requests must come from the dashboard itself.");
  }
  const response = new NextResponse(null, { status: 204 });
  response.cookies.delete(SESSION_COOKIE);
  return response;
}
