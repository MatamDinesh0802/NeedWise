import { connection } from "next/server";

/**
 * Runtime settings for the browser. NEXT_PUBLIC_* variables are inlined at `next build`
 * (in server code too), so a prebuilt Docker image can't see compose's environment.
 * This handler reads them per request instead.
 */
export async function GET() {
  await connection(); // request time, never prerendered
  const env: Record<string, string | undefined> = process.env;
  // A computed name keeps the bundler from inlining the build-time value.
  const demo = env[["NEXT_PUBLIC", "DEMO"].join("_")] === "1";
  return Response.json({ demo }, { headers: { "Cache-Control": "no-store" } });
}
