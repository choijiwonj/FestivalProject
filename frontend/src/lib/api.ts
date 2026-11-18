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
  sessionPromise ??= api<SessionInfo>("/auth/session").then(
    (r) => r.data,
    (e) => {
      sessionPromise = null; // 실패는 캐시하지 않는다: 다음 호출이 다시 확인
      throw e;
    },
  );
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
  if (body === null) {
    throw new ApiFailure(res.status, { code: "INVALID_RESPONSE", message: "서버 응답을 읽지 못했습니다.", retryable: true });
  }
  return body as Envelope<T>;
}

export function toFailure(e: unknown): ApiFailure {
  if (e instanceof ApiFailure) return e;
  console.error(e);
  return new ApiFailure(0, { code: "UNKNOWN", message: "알 수 없는 오류가 발생했습니다." });
}

/** Idempotency-Key용. crypto.randomUUID는 보안 컨텍스트(https·localhost)에서만 있다. */
export function uuid(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** GET 전용. path가 바뀌면 다시 불러오고, 늦게 온 이전 응답은 버린다. retry는 처음부터 다시, more는 다음 페이지를 이어 붙인다(목록 전용). */
export function useApi<T>(path: string | null) {
  const [state, setState] = useState<{ path: string | null; data?: T; page?: Page; error?: ApiFailure }>({ path: null });
  const [round, setRound] = useState(0);
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
  }, [path, round]);
  const fresh = state.path === path;
  const cursor = fresh && state.page?.hasNext ? state.page.nextCursor : null;
  async function more() {
    if (!path || !cursor) return;
    try {
      const r = await api<T>(`${path}${path.includes("?") ? "&" : "?"}cursor=${encodeURIComponent(cursor)}`);
      setState((s) => (s.path === path ? { path, data: [...(s.data as unknown[]), ...(r.data as unknown[])] as T, page: r.page } : s));
    } catch (e) {
      setState((s) => (s.path === path ? { ...s, error: toFailure(e) } : s));
    }
  }
  return {
    data: fresh ? state.data : undefined,
    page: fresh ? state.page : undefined,
    error: fresh ? state.error : undefined,
    loading: !!path && !fresh,
    retry: () => {
      setState({ path: null });
      setRound((n) => n + 1);
    },
    more: cursor ? more : undefined,
  };
}

export function useSession() {
  const [session, setSession] = useState<(SessionInfo & { error?: ApiFailure }) | null>(null);
  useEffect(() => {
    let alive = true;
    getSession().then(
      (s) => {
        if (alive) setSession(s);
      },
      (e) => {
        if (alive) setSession({ user: null, csrfToken: null, error: toFailure(e) });
      },
    );
    return () => {
      alive = false;
    };
  }, []);
  return session; // null = 확인 중, error = 확인 실패(로그아웃 상태 아님)
}
