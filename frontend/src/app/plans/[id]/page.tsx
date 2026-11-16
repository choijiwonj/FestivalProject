"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { useApi } from "@/lib/api";
import { BUDGET_STATUS, PLAN_STATUS, krw, time } from "@/lib/labels";
import type { TripPlan } from "@/lib/types";
import { ErrorBox, Loading } from "../../ui";

// S08 결과 + S16 충돌. 미확인 시간·비용은 0으로 보이지 않게 그대로 '미확인'(F12·B05).
export default function PlanPage() {
  const { id } = useParams<{ id: string }>();
  const plan = useApi<TripPlan>(`/trip-plans/${id}`);
  const [day, setDay] = useState(0);
  const p = plan.data;

  if (plan.loading) return <Loading />;
  if (plan.error || !p) return plan.error ? <ErrorBox error={plan.error} /> : null;
  const current = p.days[day];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">{p.title}</h1>
        <p className="text-sm">
          <strong>{PLAN_STATUS[p.status]}</strong> · 자동 저장됨
        </p>
      </div>

      {p.conflicts.length > 0 && (
        <section role="alert" className="rounded-lg border-2 border-foreground p-3 text-sm">
          <h2 className="font-semibold">조건 충돌 — 조건을 바꿔야 실행할 수 있어요</h2>
          <ul className="mt-1 list-disc pl-5">
            {p.conflicts.map((c, i) => <li key={i}>{c.message}</li>)}
          </ul>
        </section>
      )}
      {p.warnings.length > 0 && (
        <ul className="space-y-1 text-sm">
          {p.warnings.map((w) => <li key={w.code}>ℹ {w.message}</li>)}
        </ul>
      )}

      <div role="tablist" className="flex gap-2">
        {p.days.map((d, i) => (
          <button key={d.date} role="tab" aria-selected={i === day} onClick={() => setDay(i)}
            className={`rounded-full border px-3 py-1 text-sm ${i === day ? "border-foreground font-bold" : "border-black/20"}`}>
            DAY{i + 1} · {d.date.slice(5)}
          </button>
        ))}
      </div>

      <ol role="tabpanel" className="space-y-2">
        {current.stops.length === 0 && <li className="text-sm opacity-60">이 날은 아직 정해진 일정이 없어요.</li>}
        {current.stops.map((s) => (
          <li key={s.id} className="rounded-lg border border-black/10 p-3">
            <p className="text-sm opacity-60">{s.startAt ? `${time(s.startAt)} ~ ${time(s.endAt)}` : "시간 미정"}</p>
            <p className="font-semibold">{s.name}</p>
            <p className="text-sm">비용 {krw(s.costKrw)}</p>
          </li>
        ))}
      </ol>

      <section className="rounded-lg border border-black/10 p-3 text-sm">
        <h2 className="font-semibold">예산</h2>
        <p>{BUDGET_STATUS[p.budget.status] ?? p.budget.status}</p>
        <p>
          확인된 비용 {krw(p.budget.knownTotalKrw)}
          {p.budget.unknownItemCount > 0 && ` + 미확인 ${p.budget.unknownItemCount}건`}
          {p.budget.limitKrw !== null && ` / 예산 ${krw(p.budget.limitKrw)}`}
        </p>
      </section>
    </div>
  );
}
