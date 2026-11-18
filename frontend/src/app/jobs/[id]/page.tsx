"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiFailure, type ApiErrorBody, api, toFailure } from "@/lib/api";
import { STAGES } from "@/lib/labels";
import type { Job } from "@/lib/types";
import { ErrorBox, button } from "../../ui";

// S07 계획 생성 중. 2초부터 10초까지 간격을 늘려 조회, 종료 상태나 3분 경과 시 멈춘다.
const MAX_WAIT = 3 * 60 * 1000;
const NO_PLAN: ApiErrorBody = { code: "NO_PLAN", message: "계획을 만들지 못했어요.", retryable: true };

export default function JobPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [stalled, setStalled] = useState(false);
  const [round, setRound] = useState(0);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    let delay = 2000;
    const startedAt = Date.now();
    async function tick() {
      try {
        const { data } = await api<Job>(`/jobs/${id}`);
        if (!alive) return;
        const done = data.status === "SUCCEEDED" || data.status === "FAILED";
        // 성공인데 planId가 없으면 실패로 본다.
        setJob(data.status === "SUCCEEDED" && !data.result?.planId ? { ...data, status: "FAILED", error: NO_PLAN } : data);
        if (data.status === "SUCCEEDED" && data.result?.planId) router.replace(`/plans/${data.result.planId}`);
        else if (!done && Date.now() - startedAt > MAX_WAIT) setStalled(true);
        else if (!done) {
          timer = setTimeout(tick, delay);
          delay = Math.min(delay * 1.5, 10000);
        }
      } catch (e) {
        if (alive) setError(toFailure(e));
      }
    }
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [id, router, round]);

  if (error) return <ErrorBox error={error} onRetry={() => location.reload()} />;
  if (job?.status === "FAILED") {
    return (
      <div className="space-y-3 py-6">
        <ErrorBox
          error={new ApiFailure(0, job.error ?? { code: "UNKNOWN", message: "계획을 만들지 못했어요." })}
          onRetry={job.error?.retryable && job.festivalId ? () => router.push(`/festivals/${job.festivalId}/trip`) : undefined}
        />
        <Link href="/me" className="underline">마이페이지</Link>{" · "}
        <Link href="/" className="underline">홈으로</Link>
      </div>
    );
  }
  if (stalled) {
    return (
      <div className="space-y-3 py-10 text-center" role="status">
        <p className="text-lg font-semibold">예상보다 오래 걸리고 있어요</p>
        <button
          className={button}
          onClick={() => {
            setStalled(false);
            setRound((n) => n + 1);
          }}
        >
          다시 확인
        </button>
      </div>
    );
  }
  return (
    <div className="space-y-2 py-10 text-center" role="status" aria-live="polite">
      <p className="text-lg font-semibold">여행 계획을 만드는 중이에요</p>
      <p className="opacity-70">{job ? STAGES[job.stage] ?? "진행 중" : "확인 중…"}</p>
      <p className="text-sm opacity-60">화면을 닫아도 계속 진행되고, 마이페이지에서 다시 볼 수 있어요.</p>
    </div>
  );
}
