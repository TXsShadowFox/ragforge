/** Sign up (a new company and its owner), then log in. */
import type { NextRequest } from "next/server";

import { apiUrl, errorResponse, forwardedFor, logIn, readJsonObject, relay } from "@/lib/backend";
import { isSameOrigin } from "@/lib/session";

export async function POST(request: NextRequest) {
  if (!isSameOrigin(request)) {
    return errorResponse(403, "cross_origin", "Requests must come from the dashboard itself.");
  }
  const body = await readJsonObject(request);
  if (typeof body?.email !== "string" || typeof body.password !== "string") {
    return errorResponse(400, "bad_request", "Send a company name, an email and a password.");
  }
  let signup: Response;
  try {
    signup = await fetch(`${apiUrl()}/v1/auth/signup`, {
      method: "POST",
      headers: { "content-type": "application/json", ...forwardedFor(request) },
      body: JSON.stringify({
        tenant_name: body.tenant_name,
        email: body.email,
        password: body.password,
      }),
      cache: "no-store",
    });
  } catch {
    return errorResponse(502, "api_unreachable", "The RAGForge API cannot be reached.");
  }
  if (!signup.ok) return relay(signup);
  return logIn(request, { email: body.email, password: body.password });
}
