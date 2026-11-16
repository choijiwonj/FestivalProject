"use client";
import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  type ReactNode,
} from "react";
import { accountApi, type Session, type User } from "@/lib/api/account";
import { AUTH_EXPIRED_EVENT } from "@/lib/api/client";

export type AuthState = {
  session: Session | null;
  loading: boolean;
  error: Error | null;
  reload: () => void;
  setUser: (user: User) => void;
  clear: () => void;
};
export const AuthContext = createContext<AuthState | null>(null);
export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [attempt, setAttempt] = useState(0);
  const sequence = useRef(0);
  // 세션을 한 번이라도 받았는지. 받은 뒤의 재확인(창 focus)은 화면을 언마운트하지 않는다.
  const loaded = useRef(false);
  const reload = useCallback(() => setAttempt((a) => a + 1), []);
  const clear = useCallback(() => {
    sequence.current++;
    setSession({ user: null, csrfToken: null });
    setError(null);
    setLoading(false);
  }, []);
  const setUser = useCallback((user: User) => {
    sequence.current++;
    setSession((s) => (s ? { ...s, user } : s));
    setLoading(false);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const current = ++sequence.current;
    if (!loaded.current) {
      setLoading(true);
      setError(null);
    }
    accountApi.session(controller.signal).then(
      (r) => {
        if (active && sequence.current === current) {
          loaded.current = true;
          setSession(r.data);
          setError(null);
          setLoading(false);
        }
      },
      (e) => {
        if (active && sequence.current === current && e.name !== "AbortError") {
          // 재확인이 네트워크·5xx로 실패하면 기존 세션을 유지한다.
          // 실제 만료(401)는 client.ts가 AUTH_EXPIRED_EVENT → clear()로 따로 처리한다.
          if (loaded.current) return;
          setSession(null);
          setError(e);
          setLoading(false);
        }
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [attempt]);
  useEffect(() => {
    window.addEventListener("focus", reload);
    return () => window.removeEventListener("focus", reload);
  }, [reload]);
  useEffect(() => {
    window.addEventListener(AUTH_EXPIRED_EVENT, clear);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, clear);
  }, [clear]);
  return (
    <AuthContext.Provider
      value={{ session, loading, error, reload, setUser, clear }}
    >
      {children}
    </AuthContext.Provider>
  );
}
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider is required");
  return value;
}
