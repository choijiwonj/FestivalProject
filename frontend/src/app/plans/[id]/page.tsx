"use client";

import { useParams, useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { api, toFailure, useApi } from "@/lib/api";
import { BUDGET_STATUS, PLAN_STATUS, krw, time } from "@/lib/labels";
import type { PlanExport, TripPlan } from "@/lib/types";
import { button, ErrorBox, Loading } from "../../ui";

// S08 결과 + S16 충돌. 미확인 시간·비용은 0으로 보이지 않게 그대로 '미확인'(F12·B05).
export default function PlanPage() {
  const router = useRouter();
  const [regenerating, setRegenerating] = useState(false);
  const [request, setRequest] = useState("");
  const [regenerationError, setRegenerationError] = useState<string | null>(null);
  const regenerationKey = useRef<string | null>(null);
  const { id } = useParams<{ id: string }>();
  const plan = useApi<TripPlan>(`/trip-plans/${id}`);
  const [day, setDay] = useState(0);
  const [exporting, setExporting] = useState<"PDF" | "PNG" | null>(null);
  const [exportResult, setExportResult] = useState<PlanExport | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const p = plan.data;

  async function regenerate() {
    if (!p || regenerating) return;
    setRegenerating(true);
    setRegenerationError(null);
    regenerationKey.current ??= crypto.randomUUID();
    try {
      const accepted = await api<{jobId: string}>(`/trip-plans/${p.id}/regenerations`, {
        method: "POST", headers: {"Idempotency-Key": regenerationKey.current},
        json: {...p.inputSnapshot, baseVersion: p.version,
          additionalRequests: request.trim() || p.inputSnapshot.additionalRequests || null},
      });
      router.push(`/jobs/${accepted.data.jobId}`);
    } catch (error) {
      setRegenerationError(toFailure(error).message);
      setRegenerating(false);
    }
  }

  async function createExport(format: "PDF" | "PNG") {
    if (!p || exporting) return;
    setExporting(format);
    setExportError(null);
    setExportResult(null);
    try {
      const accepted = await api<{ exportId: string }>(`/trip-plans/${p.id}/exports`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        json: { format, version: p.version },
      });
      for (;;) {
        const result = (await api<PlanExport>(`/exports/${accepted.data.exportId}`)).data;
        if (result.status === "SUCCEEDED") {
          setExportResult(result);
          break;
        }
        if (result.status === "FAILED") throw new Error(result.error?.message ?? "파일을 만들지 못했습니다.");
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
    } catch (error) {
      setExportError(error instanceof Error ? error.message : toFailure(error).message);
    } finally {
      setExporting(null);
    }
  }

  if (plan.loading) return <Loading />;
  if (plan.error || !p) return plan.error ? <ErrorBox error={plan.error} /> : null;
  const current = p.days[day];
  const modes: Record<string, string> = {CAR:"자동차", PUBLIC_TRANSIT:"대중교통", BUS:"버스", SUBWAY:"지하철",
    TRAIN:"열차", EXPRESS_BUS:"고속버스", INTERCITY_BUS:"시외버스", WALK:"도보", TRANSFER_BUFFER:"승차 준비시간(가정)"};
  const stopName = (id: string) => id === "ORIGIN" ? (p.inputSnapshot.origin?.label || "출발지")
    : p.days.flatMap(d => d.stops).find(s => s.id === id)?.name || "장소 미확인";

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
            {s.recommendationReason && <p className="text-sm">추천 이유: {s.recommendationReason}</p>}
            {s.address && <p className="text-sm">주소: {s.address}</p>}
          </li>
        ))}
      </ol>

      <section className="space-y-2 text-sm">
        <h2 className="font-semibold">이동 경로</h2>
        {!current.legs?.length && <p>이동 경로 미확인</p>}
        {current.legs?.map((leg, i) => <div key={i} className="rounded-lg border border-black/10 p-3 space-y-1">
          <p className="font-semibold">{stopName(leg.fromStopId)} → {stopName(leg.toStopId)}</p>
          <p>{modes[leg.mode] || leg.mode} · {leg.durationMinutes == null ? "시간 미확인" : `${leg.durationMinutes}분`} · {leg.distanceMeters == null ? "거리 미확인" : `${leg.distanceMeters.toLocaleString()}m`}</p>
          <p>예상 출발 {time(leg.departureAt ?? null)} / 도착 {time(leg.arrivalAt ?? null)}</p>
          {leg.segments?.map((s, j) => <p key={j}>{modes[s.mode] || s.mode} · {s.fromName || "출발 지점 미확인"} → {s.toName || "도착 지점 미확인"} · {s.durationMinutes == null ? "시간 미확인" : `${s.durationMinutes}분`} {s.lines?.map(l => l.busNo || l.name).filter(Boolean).join(", ")}{s.walkabilityStatus === "UNCONFIRMED" && " · 실제 보행 가능 여부 미확인"}</p>)}
          {leg.serviceTimeStatus === "UNCONFIRMED" && <p>실제 운행 시각·대기시간·막차 미확인</p>}
        </div>)}
      </section>

      <section className="space-y-2 rounded-lg border border-black/10 p-3 text-sm">
        <h2 className="font-semibold">다시 추천받기</h2>
        <p>현재 여행 조건으로 새 계획을 만듭니다. 기존 계획은 보관됩니다.</p>
        <label className="block">변경할 요청사항 (선택)
          <textarea className="block w-full rounded border p-2" value={request} maxLength={1000}
            onChange={e => {setRequest(e.target.value); regenerationKey.current = null;}} disabled={regenerating}/>
        </label>
        <button className={button} onClick={regenerate} disabled={regenerating}>{regenerating ? "재추천 요청 중…" : "다시 추천받기"}</button>
        {regenerationError && <p role="alert">{regenerationError}</p>}
        {p.parentPlanId && <a className="block underline" href={`/plans/${p.parentPlanId}`}>이전 계획 보기</a>}
      </section>

      <section className="rounded-lg border border-black/10 p-3 text-sm">
        <h2 className="font-semibold">예산</h2>
        <p>{BUDGET_STATUS[p.budget.status] ?? p.budget.status}</p>
        <p>
          확인된 비용 {krw(p.budget.knownTotalKrw)}
          {p.budget.unknownItemCount > 0 && ` + 미확인 ${p.budget.unknownItemCount}건`}
          {p.budget.limitKrw !== null && ` / 예산 ${krw(p.budget.limitKrw)}`}
        </p>
      </section>

      <section className="space-y-3 rounded-lg border border-black/10 p-3 text-sm">
        <div>
          <h2 className="font-semibold">계획 내보내기</h2>
          <p className="opacity-70">현재 계획 v{p.version}의 일정·이동 구간·예산·확인할 사항을 파일로 저장해요.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className={button} disabled={!!exporting || p.status === "INFEASIBLE"}
            onClick={() => createExport("PDF")}>
            {exporting === "PDF" ? "PDF 만드는 중…" : "PDF로 저장"}
          </button>
          <button className={button} disabled={!!exporting || p.status === "INFEASIBLE"}
            onClick={() => createExport("PNG")}>
            {exporting === "PNG" ? "이미지 만드는 중…" : "이미지로 저장"}
          </button>
        </div>
        {p.status === "INFEASIBLE" && <p role="alert">조건 충돌을 해결한 뒤 내보낼 수 있어요.</p>}
        {exportError && <p role="alert" className="text-red-700">{exportError}</p>}
        {exportResult?.downloadUrl && (
          <a className="inline-block font-semibold underline" href={exportResult.downloadUrl}>
            {exportResult.filename ?? "완성된 파일"} 다운로드
          </a>
        )}
      </section>
    </div>
  );
}
