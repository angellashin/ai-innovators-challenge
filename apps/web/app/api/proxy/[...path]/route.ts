import { NextRequest } from "next/server";

const hopByHopHeaders = new Set(["connection", "content-length", "content-encoding", "host"]);

function backendUrl() {
  return (process.env.REPLAN_BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
}

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const target = `${backendUrl()}/${path.join("/")}${request.nextUrl.search}`;
  const headers = new Headers(request.headers);
  hopByHopHeaders.forEach((header) => headers.delete(header));
  const token = process.env.REPLAN_DEMO_TOKEN;
  const cookieHeader = request.headers.get("cookie") || "";
  const session = request.cookies.get("replan_session")?.value
    || cookieHeader.match(/(?:^|;\s*)replan_session=([^;]+)/)?.[1];
  if (session && !headers.get("authorization")) headers.set("authorization", `Bearer ${session}`);
  // Unauthenticated demo traffic uses the server demo token; logged-in users
  // keep their own bearer token so project ownership is enforced by the API.
  if (!headers.get("authorization") && token) headers.set("authorization", `Bearer ${token}`);

  const init: RequestInit = { method: request.method, headers, redirect: "manual" };
  if (request.method !== "GET" && request.method !== "HEAD") init.body = await request.arrayBuffer();

  const response = await fetch(target, init);
  const responseHeaders = new Headers(response.headers);
  hopByHopHeaders.forEach((header) => responseHeaders.delete(header));
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers: responseHeaders });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
