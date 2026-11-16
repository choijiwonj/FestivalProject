"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiFailure, api, toFailure } from "@/lib/api";
import { STAGES } from "@/lib/labels";
import type { Job } from "@/lib/types";
import { ErrorBox } from "../../ui";

// S07 계획 생성 중. 2초부터 10초까지 간격을 늘려 조회, 종료 상태에서 멈춘다.
export default function JobPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<ApiFailure | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    let delay = 2000;
    async function tick() {
      try {
        const { data } = await api<Job>(`/jobs/${id}`);
        if (!alive) return;
        setJob(data);
        if (data.status === "SUCCEEDED" && data.result?.planId) router.replace(`/plans/${data.result.planId}`);
        else if (data.status !== "FAILED") {
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
  }, [id, router]);

  if (error) return <ErrorBox error={error} onRetry={() => location.reload()} />;
  if (job?.status === "FAILED") {
    return (
      <div className="space-y-3 py-6">
        <p role="alert">{job.error?.message ?? "계획을 만들지 못했어요."}</p>
        <Link href="/me" className="underline">마이페이지에서 요청 보기</Link>
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
