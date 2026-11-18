"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiFailure, api, loginUrl, resetSession, toFailure, useApi, useSession } from "@/lib/api";
import { PLAN_STATUS, STAGES } from "@/lib/labels";
import type { PlanSummary } from "@/lib/types";
import { ErrorBox, Loading, button } from "../ui";

type RequestSummary = { id: string; jobId: string; stage: string; primaryFestival: { name: string | null } };

// S13 마이페이지 + S25 로그아웃 확인.
export default function MePage() {
  const router = useRouter();
  const session = useSession();
  const user = session?.user;
  const plans = useApi<PlanSummary[]>(user ? "/me/trip-plans" : null);
  const active = useApi<RequestSummary[]>(user ? "/me/trip-requests?status=ACTIVE" : null);
  const [confirmLogout, setConfirmLogout] = useState(false);
  const [loginFailed, setLoginFailed] = useState(false);
  const [logoutError, setLogoutError] = useState<ApiFailure | null>(null);

  useEffect(() => {
    // 로그인 실패 콜백은 /me?login=failed 로 돌아온다
    const failed = new URLSearchParams(window.location.search).get("login") === "failed";
    if (failed) queueMicrotask(() => setLoginFailed(true));
  }, []);

  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch (e) {
      setLogoutError(toFailure(e)); // 실패하면 페이지에 남는다
      return;
    }
    resetSession(); // 개인 캐시 제거
    router.replace("/");
  }

  if (session === null) return <Loading />;
  if (session.error) return <ErrorBox error={session.error} onRetry={() => window.location.reload()} />;
  if (!user) {
    return (
      <div className="space-y-3 py-6 text-center">
        {loginFailed && <p role="alert">로그인하지 못했어요. 다시 시도해 주세요.</p>}
        <p>로그인하면 만든 여행 계획을 다시 볼 수 있어요.</p>
        <a href={loginUrl("/me")} className={`${button} inline-block`}>카카오로 로그인</a>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">{user.nickname}님</h1>

      {active.error && <ErrorBox error={active.error} />}
      {active.data && active.data.length > 0 && (
        <section className="space-y-2">
          <h2 className="font-semibold">만드는 중인 계획</h2>
          {active.data.map((r) => (
            <Link key={r.id} href={`/jobs/${r.jobId}`} className="block rounded-lg border border-black/10 p-3 text-sm">
              {r.primaryFestival.name} · {STAGES[r.stage] ?? "진행 중"}
            </Link>
          ))}
        </section>
      )}

      <section className="space-y-2">
        <h2 className="font-semibold">내 여행 계획</h2>
        {plans.loading && <Loading />}
        {plans.error && <ErrorBox error={plans.error} />}
        {plans.data?.length === 0 && <p className="text-sm opacity-60">아직 만든 계획이 없어요.</p>}
        {plans.data?.map((p) => (
          <Link key={p.id} href={`/plans/${p.id}`} className="block rounded-lg border border-black/10 p-3">
            <p className="font-semibold">{p.title}</p>
            <p className="text-sm opacity-70">{p.startDate} ~ {p.endDate} · {PLAN_STATUS[p.status] ?? p.status}</p>
          </Link>
        ))}
      </section>

      {confirmLogout ? (
        <div className="space-y-2 rounded-lg border border-black/15 p-3">
          <p>로그아웃할까요? 저장된 계획은 그대로 남아요.</p>
          {logoutError && <ErrorBox error={logoutError} />}
          <div className="flex gap-2">
            <button onClick={() => setConfirmLogout(false)} className="rounded-lg border border-black/20 px-4 py-2">취소</button>
            <button onClick={logout} className={`${button} flex-1`}>로그아웃</button>
          </div>
        </div>
      ) : (
        <button onClick={() => setConfirmLogout(true)} className="text-sm underline">로그아웃</button>
      )}
    </div>
  );
}
