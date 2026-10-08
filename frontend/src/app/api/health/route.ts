/** Liveness for Docker: the dashboard's server is running. It calls nothing else. */
export function GET() {
  return Response.json({ status: "ok" });
}
