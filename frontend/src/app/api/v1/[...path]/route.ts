/** /api/v1/<path> -> the API's /v1/<path>, with the login token from the httpOnly cookie. */
import type { NextRequest } from "next/server";

import { forwardToApi } from "@/lib/backend";

async function handler(request: NextRequest, context: RouteContext<"/api/v1/[...path]">) {
  const { path } = await context.params;
  return forwardToApi(request, path);
}

export { handler as DELETE, handler as GET, handler as PATCH, handler as POST, handler as PUT };
