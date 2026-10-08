/** Settings the browser needs, read when the server runs (not when the image is built). */
import { connection } from "next/server";

export async function GET() {
  await connection();
  const publicApiUrl = (process.env.PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
  return Response.json({ publicApiUrl });
}
