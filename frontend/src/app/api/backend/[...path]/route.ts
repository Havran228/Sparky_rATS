import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const token = process.env.SPARKY_API_TOKEN;
  if (!token) {
    return NextResponse.json(
      { detail: "Set SPARKY_API_TOKEN for the Next.js server so it can call the API." },
      { status: 503 },
    );
  }
  const { path } = await context.params;
  const base = process.env.SPARKY_API_BASE ?? "http://127.0.0.1:8002";
  const target = new URL(`${base.replace(/\/$/, "")}/${path.join("/")}`);
  target.search = request.nextUrl.search;
  const headers = new Headers();
  headers.set("Authorization", `Bearer ${token}`);
  const contentType = request.headers.get("content-type");
  if (contentType) {
    headers.set("content-type", contentType);
  }
  const init: RequestInit = { method: request.method, headers, cache: "no-store" };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }
  const response = await fetch(target, init);
  return new NextResponse(await response.arrayBuffer(), {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
    },
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
