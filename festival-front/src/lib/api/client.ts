export const AUTH_EXPIRED_EVENT = "festival:session-expired";
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code = "UNKNOWN",
    public retryable = false,
    public fields: { path: string; reason: string }[] = [],
    public requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
// 서버가 오류 본문(JSON)을 주지 않을 때(프록시 HTML 등) 상태 코드로 고르는 기본 문구
function fallbackMessage(status: number, write: boolean) {
  if (status === 401) return "로그인 상태를 다시 확인해 주세요.";
  if (status === 403) return "이 작업을 할 권한이 없어요. 새로고침 후 다시 시도해 주세요.";
  if (status === 404) return "요청한 정보를 찾을 수 없어요.";
  if (status === 409) return "다른 곳에서 먼저 바뀌었어요. 최신 내용을 확인해 주세요.";
  if (status === 429) return "요청이 많아요. 잠시 후 다시 시도해 주세요.";
  if (status >= 500) return "서버가 잠시 응답하지 않아요. 잠시 후 다시 시도해 주세요.";
  return write ? "저장하지 못했어요. 다시 시도해 주세요." : "정보를 불러오지 못했어요.";
}

export async function apiGet<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  return apiRequest<T>(path, { signal });
}

export async function apiRequest<T>(
  path: string,
  options: {
    method?: string;
    json?: unknown;
    csrfToken?: string | null;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL || "/api/v1";
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.json !== undefined) headers["Content-Type"] = "application/json";
  const write = !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase());
  if (write) {
    if (!options.csrfToken) {
      // 세션 정보가 불완전 → 같은 오류를 반복하지 않도록 세션을 비워 로그인 안내로 보낸다
      if (typeof window !== "undefined")
        window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
      throw new ApiError(
        401,
        "로그인 상태를 다시 확인해 주세요.",
        "CSRF_MISSING",
      );
    }
    headers["X-CSRF-Token"] = options.csrfToken;
  }
  let response: Response;
  try {
    response = await fetch(`${base.replace(/\/$/, "")}${path}`, {
      signal: options.signal,
      method,
      headers,
      body:
        options.json === undefined ? undefined : JSON.stringify(options.json),
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError(
      0,
      "서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.",
      "NETWORK_ERROR",
      true,
    );
  }
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined")
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    const payload = await response.json().catch(() => null);
    throw new ApiError(
      response.status,
      payload?.error?.message ?? fallbackMessage(response.status, write),
      payload?.error?.code,
      payload?.error?.retryable ?? (response.status === 429 || response.status >= 500),
      payload?.error?.fields,
      payload?.meta?.requestId,
    );
  }
  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError(
      response.status,
      "서버 응답을 읽지 못했어요. 잠시 후 다시 시도해 주세요.",
      "BAD_RESPONSE",
      true,
    );
  }
}
