"use client";

import { useEffect, useState } from "react";

export type ApiErrorBody = {
  code: string;
  message: string;
  fields?: { path: string; reason: string }[];
  retryable?: boolean;
};

export class ApiFailure extends Error {
  constructor(public status: number, public body: ApiErrorBody) {
    super(body.message);
  }
}

export type Page = { nextCursor: string | null; hasNext: boolean };
type Envelope<T> = { data: T; page?: Page; meta?: Record<string, unknown> };
export type SessionInfo = { user: { id: string; nickname: string; avatarUrl: string | null } | null; csrfToken: string | null };

let sessionPromise: Promise<SessionInfo> | null = null;

export function getSession(): Promise<SessionInfo> {
  sessionPromise ??= api<SessionInfo>("/auth/session").then((r) => r.data);
  return sessionPromise;
}

export function resetSession() {
  sessionPromise = null;
}

export function loginUrl(returnTo: string) {
  const provider = process.env.NEXT_PUBLIC_LOGIN_PROVIDER ?? "kakao";
  return `/api/v1/auth/login/${provider}?returnTo=${encodeURIComponent(returnTo)}`;
}

export async function api<T>(
  path: string,
  init: Omit<RequestInit, "body"> & { json?: unknown } = {},
): Promise<Envelope<T>> {
  const { json, ...rest } = init;
  const method = rest.method ?? "GET";
  const headers = new Headers(rest.headers);
  if (json !== undefined) headers.set("Content-Type", "application/json");
  if (method !== "GET") headers.set("X-CSRF-Token", (await getSession()).csrfToken ?? "");
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      ...rest,
      headers,
      body: json !== undefined ? JSON.stringify(json) : undefined,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiFailure(0, { code: "NETWORK", message: "네트워크에 연결할 수 없습니다.", retryable: true });
  }
  if (res.status === 204) return { data: null as T };
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiFailure(res.status, body?.error ?? { code: "UNKNOWN", message: "요청을 처리하지 못했습니다.", retryable: true });
  }
  return body as Envelope<T>;
}

export function toFailure(e: unknown): ApiFailure {
  return e instanceof ApiFailure ? e : new ApiFailure(0, { code: "UNKNOWN", message: "알 수 없는 오류가 발생했습니다." });
}

/** GET 전용. path가 바뀌면 다시 불러오고, 늦게 온 이전 응답은 버린다. */
export function useApi<T>(path: string | null) {
  const [state, setState] = useState<{ path: string | null; data?: T; page?: Page; error?: ApiFailure }>({ path: null });
  useEffect(() => {
    if (!path) return;
    let alive = true;
    api<T>(path).then(
      (r) => {
        if (alive) setState({ path, data: r.data, page: r.page });
      },
      (e) => {
        if (alive) setState({ path, error: toFailure(e) });
      },
    );
    return () => {
      alive = false;
    };
  }, [path]);
  const fresh = state.path === path;
  return {
    data: fresh ? state.data : undefined,
    page: fresh ? state.page : undefined,
    error: fresh ? state.error : undefined,
    loading: !!path && !fresh,
  };
}

export function useSession() {
  const [session, setSession] = useState<SessionInfo | null>(null);
  useEffect(() => {
    let alive = true;
    getSession().then(
      (s) => {
        if (alive) setSession(s);
      },
      () => {
        if (alive) setSession({ user: null, csrfToken: null });
      },
    );
    return () => {
      alive = false;
    };
  }, []);
  return session; // null = 확인 중
}
