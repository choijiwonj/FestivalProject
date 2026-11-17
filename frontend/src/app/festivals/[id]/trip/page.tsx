"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ApiFailure, api, loginUrl, toFailure, useApi, useSession } from "@/lib/api";
import { INTERESTS, LODGING, time } from "@/lib/labels";
import type { FestivalDetail, Location, Program, UserSettings, ValidationResult } from "@/lib/types";
import { ErrorBox, Loading, button, input } from "../../../ui";

// S04 기본 조건 → S05 취향·필수 일정 → S06 입력 확인. 세 단계가 하나의 입력 상태를 편집한다(F06~F09).
type Form = {
  startDate?: string;
  endDate?: string;
  origin?: Location;
  transport?: string;
  travelers?: number;
  budgetKrw?: number | null;
  interests?: string[];
  style?: string;
  lodgingPreference?: string | null;
  requiredProgramIds?: string[];
  additionalRequests?: string;
};

function definedOnly(f: Form): Form {
  return Object.fromEntries(Object.entries(f).filter(([, v]) => v !== undefined)) as Form;
}

// validate_trip과 같은 규칙: 시작·종료 시각이 확정되고, 시작일(KST, startAt 앞 10자)이 여행 날짜 안이어야 고를 수 있다.
function pickable(p: Program, f: Form) {
  const day = p.startAt?.slice(0, 10);
  return !!(day && p.endAt && f.startDate && f.endDate && day >= f.startDate && day <= f.endDate);
}

export default function TripPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const session = useSession();
  const fest = useApi<FestivalDetail>(`/festivals/${id}`);
  const programs = useApi<Program[]>(`/festivals/${id}/programs`);
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<Form>({});
  const [checked, setChecked] = useState<ValidationResult | null>(null);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [busy, setBusy] = useState(false);
  const idemKey = useRef<string | null>(null);

  // F23: 저장된 기본값은 사용자가 아직 고르지 않은 칸에만 채운다(늦게 와도 입력을 덮어쓰지 않음, T13).
  const loggedIn = !!session?.user;
  useEffect(() => {
    if (!loggedIn) return;
    api<UserSettings>("/me/settings").then(
      ({ data: { travelDefaults: d } }) =>
        setForm((f) => ({
          ...definedOnly({
            origin: d.origin ?? undefined,
            transport: d.transport ?? undefined,
            travelers: d.travelers ?? undefined,
            interests: d.interests.length ? d.interests : undefined,
            style: d.style ?? undefined,
            lodgingPreference: d.lodgingPreference ?? undefined,
          }),
          ...definedOnly(f),
        })),
      (e) => console.warn("기본값을 불러오지 못했어요", e), // 기본값을 못 불러와도 입력은 계속할 수 있다
    );
  }, [loggedIn]);

  function update(patch: Form) {
    setForm((f) => {
      const next = { ...f, ...patch };
      // 날짜가 바뀌어 범위를 벗어난 필수 프로그램은 선택에서 뺀다
      if (next.requiredProgramIds && programs.data) {
        next.requiredProgramIds = next.requiredProgramIds.filter((pid) =>
          programs.data!.some((p) => p.id === pid && pickable(p, next)));
      }
      return next;
    });
    setChecked(null);
    idemKey.current = null; // 조건이 바뀌면 새 요청 키
  }

  function payload() {
    return {
      primaryFestivalId: id,
      additionalFestivalIds: [],
      startDate: form.startDate,
      endDate: form.endDate,
      origin: form.origin,
      transport: form.transport,
      travelers: form.travelers ?? 1,
      budgetKrw: form.budgetKrw ?? null,
      interests: form.interests ?? [],
      style: form.style ?? "RELAXED",
      lodgingPreference: form.lodgingPreference ?? null,
      requiredPlaceIds: [],
      requiredProgramIds: form.requiredProgramIds ?? [],
      additionalRequests: form.additionalRequests?.trim() || null,
    };
  }

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(toFailure(e));
    } finally {
      setBusy(false);
    }
  }

  const validate = () =>
    run(async () => {
      const r = await api<ValidationResult>("/trip-requests/validate", { method: "POST", json: payload() });
      setChecked(r.data);
      setStep(2);
    });

  const create = () =>
    run(async () => {
      idemKey.current ??= crypto.randomUUID(); // 중복 클릭은 같은 키 → 한 작업(T03)
      const r = await api<{ jobId: string }>("/trip-requests", {
        method: "POST",
        json: payload(),
        headers: { "Idempotency-Key": idemKey.current },
      });
      router.push(`/jobs/${r.data.jobId}`);
    });

  if (session === null || fest.loading) return <Loading />;
  if (session.error) return <ErrorBox error={session.error} onRetry={() => location.reload()} />;
  if (!session.user) {
    return (
      <div className="space-y-3 py-6 text-center">
        <p>여행 계획을 만들려면 로그인이 필요해요.</p>
        <a href={loginUrl(`/festivals/${id}/trip`)} className={`${button} inline-block`}>로그인</a>
      </div>
    );
  }
  if (fest.error || !fest.data) return fest.error ? <ErrorBox error={fest.error} /> : null;
  const f = fest.data;
  const step0Done = !!(form.startDate && form.endDate && form.origin && form.transport);
  const multiDay = !!form.startDate && !!form.endDate && form.startDate !== form.endDate;

  return (
    <div className="space-y-4">
      <p className="text-sm opacity-60">{["1/3 기본 조건", "2/3 취향·필수 일정", "3/3 입력 확인"][step]}</p>
      <h1 className="text-xl font-bold">{f.name}</h1>
      {error && <ErrorBox error={error} />}

      {step === 0 && (
        <>
          <fieldset className="grid grid-cols-2 gap-2">
            <legend className="mb-1 text-sm font-semibold">여행 날짜 (축제 전후 2일 안)</legend>
            <label className="text-sm">
              시작일
              <input type="date" min={f.dateSelection.min} max={f.dateSelection.max} value={form.startDate ?? ""}
                onChange={(e) => {
                  const startDate = e.target.value;
                  update({ startDate, endDate: form.endDate && form.endDate < startDate ? undefined : form.endDate });
                }} className={input} />
            </label>
            <label className="text-sm">
              종료일
              <input type="date" min={form.startDate ?? f.dateSelection.min} max={f.dateSelection.max} value={form.endDate ?? ""}
                onChange={(e) => update({ endDate: e.target.value })} className={input} />
            </label>
          </fieldset>
          <OriginPicker value={form.origin} onChange={(origin) => update({ origin })} />
          <fieldset>
            <legend className="mb-1 text-sm font-semibold">교통수단</legend>
            <div className="flex gap-4">
              {[["CAR", "자가용"], ["PUBLIC_TRANSIT", "대중교통"]].map(([v, l]) => (
                <label key={v} className="flex items-center gap-1">
                  <input type="radio" name="transport" checked={form.transport === v} onChange={() => update({ transport: v })} />
                  {l}
                </label>
              ))}
            </div>
          </fieldset>
          <button disabled={!step0Done} onClick={() => setStep(1)} className={`${button} w-full`}>다음</button>
        </>
      )}

      {step === 1 && (
        <>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-sm">
              인원
              <input type="number" min={1} max={20} placeholder="1" value={form.travelers ?? ""}
                onChange={(e) => update({ travelers: e.target.value === "" ? undefined : Number(e.target.value) })} className={input} />
            </label>
            <label className="text-sm">
              총 예산(원, 비우면 미입력)
              <input type="number" min={0} step={10000} value={form.budgetKrw ?? ""}
                onChange={(e) => update({ budgetKrw: e.target.value === "" ? null : Number(e.target.value) })} className={input} />
            </label>
          </div>
          <fieldset>
            <legend className="mb-1 text-sm font-semibold">관심사</legend>
            <div className="flex flex-wrap gap-3">
              {Object.entries(INTERESTS).map(([v, l]) => {
                const on = form.interests?.includes(v) ?? false;
                return (
                  <label key={v} className="flex items-center gap-1">
                    <input type="checkbox" checked={on} onChange={() =>
                      update({ interests: on ? form.interests!.filter((x) => x !== v) : [...(form.interests ?? []), v] })} />
                    {l}
                  </label>
                );
              })}
            </div>
          </fieldset>
          <fieldset>
            <legend className="mb-1 text-sm font-semibold">여행 스타일</legend>
            <div className="flex gap-4">
              {[["RELAXED", "여유롭게"], ["PACKED", "알차게"]].map(([v, l]) => (
                <label key={v} className="flex items-center gap-1">
                  <input type="radio" name="style" checked={(form.style ?? "RELAXED") === v} onChange={() => update({ style: v })} />
                  {l}
                </label>
              ))}
            </div>
          </fieldset>
          {multiDay && (
            <label className="block text-sm">
              숙박 선호
              <select value={form.lodgingPreference ?? "ANY"} onChange={(e) => update({ lodgingPreference: e.target.value })} className={input}>
                {Object.entries(LODGING).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
          )}
          <fieldset>
            <legend className="mb-1 text-sm font-semibold">꼭 볼 프로그램</legend>
            {programs.error && <ErrorBox error={programs.error} />}
            {programs.data?.filter((p) => pickable(p, form)).map((p) => {
              const on = form.requiredProgramIds?.includes(p.id) ?? false;
              return (
                <label key={p.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={on} onChange={() => update({
                    requiredProgramIds: on ? form.requiredProgramIds!.filter((x) => x !== p.id) : [...(form.requiredProgramIds ?? []), p.id],
                  })} />
                  {p.startAt!.slice(5, 10)} {time(p.startAt)} · {p.name}
                </label>
              );
            })}
          </fieldset>
          <label className="block text-sm">
            추가 요청 (선택)
            <textarea maxLength={1000} value={form.additionalRequests ?? ""}
              onChange={(e) => update({ additionalRequests: e.target.value })} className={input} />
          </label>
          <div className="flex gap-2">
            <button onClick={() => setStep(0)} className="rounded-lg border border-black/20 px-4">이전</button>
            <button disabled={busy} onClick={validate} className={`${button} flex-1`}>{busy ? "확인 중…" : "입력 확인"}</button>
          </div>
        </>
      )}

      {step === 2 && checked && (
        <>
          <dl className="grid grid-cols-[5rem_1fr] gap-y-2 text-sm">
            <dt className="opacity-60">일정</dt>
            <dd>{form.startDate} ~ {form.endDate} ({checked.nights === 0 ? "당일" : `${checked.nights}박 ${checked.days}일`})</dd>
            <dt className="opacity-60">출발지</dt>
            <dd>{form.origin?.label}</dd>
            <dt className="opacity-60">교통</dt>
            <dd>{form.transport === "CAR" ? "자가용" : "대중교통"}</dd>
            <dt className="opacity-60">인원·예산</dt>
            <dd>{form.travelers ?? 1}명 · {form.budgetKrw == null ? "예산 미입력" : `총 ${form.budgetKrw.toLocaleString("ko-KR")}원`}</dd>
          </dl>
          {checked.warnings.map((w) => <p key={w.code} className="text-sm">ℹ {w.message}</p>)}
          <div className="flex gap-2">
            <button onClick={() => setStep(1)} className="rounded-lg border border-black/20 px-4">수정</button>
            <button disabled={busy} onClick={create} className={`${button} flex-1`}>{busy ? "요청 중…" : "이 조건으로 계획 만들기"}</button>
          </div>
        </>
      )}
    </div>
  );
}

function OriginPicker({ value, onChange }: { value?: Location; onChange: (l: Location) => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Location[] | null>(null);
  const [error, setError] = useState<ApiFailure | null>(null);

  async function search() {
    setError(null);
    try {
      setResults((await api<Location[]>(`/locations/search?q=${encodeURIComponent(q.trim())}`)).data);
    } catch (e) {
      setError(toFailure(e));
    }
  }

  return (
    <fieldset>
      <legend className="mb-1 text-sm font-semibold">출발지 {value && <span className="font-normal">· {value.label}</span>}</legend>
      <div className="flex gap-2">
        <label htmlFor="origin-q" className="sr-only">출발지 검색</label>
        <input id="origin-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="역·주소·장소 이름" className={input}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); search(); } }} />
        <button type="button" disabled={q.trim().length < 2} onClick={search} className="shrink-0 rounded-lg border border-black/20 px-3 disabled:opacity-40">검색</button>
      </div>
      {error && <ErrorBox error={error} />}
      {results?.length === 0 && <p className="text-sm opacity-60">검색 결과가 없어요.</p>}
      <ul className="mt-1 space-y-1">
        {results?.map((l) => (
          <li key={l.locationId}>
            <button type="button" onClick={() => { onChange(l); setResults(null); }} className="text-left text-sm underline">{l.label}</button>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}
