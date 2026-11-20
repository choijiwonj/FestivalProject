import { NextRequest, NextResponse } from "next/server";
import { safeReturnTo } from "@/lib/navigation";
let warnedMissingBackend = false;
// Keep OAuth cookies on the frontend origin and show a useful screen for 503s.
export async function GET(request: NextRequest) {
  const returnTo = safeReturnTo(request.nextUrl.searchParams.get("returnTo"));
  const fail = (reason = "provider_unavailable") =>
    NextResponse.redirect(
      new URL(
        `/login?returnTo=${encodeURIComponent(returnTo)}&error=${reason}`,
        request.url,
      ),
    );
  const provider =
    process.env.NEXT_PUBLIC_LOGIN_PROVIDER === "dev" ? "dev" : "kakao";
  const backend =
    process.env.API_PROXY_TARGET ||
    process.env.BACKEND_URL ||
    "http://127.0.0.1:8000";
  if (
    process.env.NODE_ENV === "production" &&
    !process.env.API_PROXY_TARGET &&
    !process.env.BACKEND_URL &&
    !warnedMissingBackend
  ) {
    warnedMissingBackend = true;
    console.error(
      "auth start: API_PROXY_TARGET/BACKEND_URL is not set; using http://127.0.0.1:8000",
    );
  }
  // Forward the client IP so backend per-IP limits don't count this server for everyone.
  // NextRequest has no .ip since Next 15; the platform proxy sets x-forwarded-for.
  const headers: Record<string, string> = {};
  const forwardedFor = request.headers.get("x-forwarded-for");
  const realIp = request.headers.get("x-real-ip");
  if (forwardedFor) headers["x-forwarded-for"] = forwardedFor;
  else if (realIp) headers["x-forwarded-for"] = realIp;
  if (realIp) headers["x-real-ip"] = realIp;
  try {
    const upstream = await fetch(
      `${backend.replace(/\/$/, "")}/api/v1/auth/login/${provider}?returnTo=${encodeURIComponent(returnTo)}`,
      {
        headers,
        redirect: "manual",
        cache: "no-store",
        signal: AbortSignal.timeout(15000),
      },
    );
    if (upstream.status === 429) return fail("rate_limited");
    if (![302, 303, 307].includes(upstream.status)) {
      if (upstream.status !== 503)
        console.error("auth start: unexpected backend status", upstream.status);
      return fail();
    }
    const location = upstream.headers.get("location");
    if (!location) return fail();
    const target = new URL(location, request.url);
    if (
      target.origin !== request.nextUrl.origin &&
      (target.protocol !== "https:" || target.hostname !== "kauth.kakao.com")
    )
      return fail();
    const response = NextResponse.redirect(target, 302);
    for (const cookie of upstream.headers.getSetCookie())
      response.headers.append("Set-Cookie", cookie);
    response.headers.set("Cache-Control", "no-store");
    return response;
  } catch (e) {
    console.error("auth start failed", e);
    return fail();
  }
}
