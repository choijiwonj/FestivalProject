"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useApi } from "@/lib/api";
import { REGIONS, krw, time } from "@/lib/labels";
import type { FestivalDetail, Program } from "@/lib/types";
import { ErrorBox, Loading, button } from "../../ui";

// S03 축제 상세. 정보가 없으면 '미확인'으로 표시하고 채워 넣지 않는다(F03·B05).
export default function FestivalPage() {
  const { id } = useParams<{ id: string }>();
  const fest = useApi<FestivalDetail>(`/festivals/${id}`);
  const programs = useApi<Program[]>(`/festivals/${id}/programs`);
  const f = fest.data;

  if (fest.loading) return <Loading />;
  if (fest.error || !f) return fest.error ? <ErrorBox error={fest.error} /> : null;

  return (
    <div className="space-y-5 pb-20">
      <div>
        <h1 className="text-xl font-bold">{f.name}</h1>
        <p className="text-sm opacity-70">
          {REGIONS[f.region] ?? f.region} · {f.startDate} ~ {f.endDate}
          {f.status === "CANCELLED" && " · 취소됨"}
        </p>
      </div>
      {f.description && <p>{f.description}</p>}

      <dl className="grid grid-cols-[5rem_1fr] gap-y-2 text-sm">
        <dt className="opacity-60">장소</dt>
        <dd>{f.venue?.label ?? "미확인"}</dd>
        <dt className="opacity-60">운영시간</dt>
        <dd>{f.openingHours ?? "미확인"}</dd>
        <dt className="opacity-60">입장료</dt>
        <dd>{f.admission ? krw(f.admission.amountKrw) : "미확인"}</dd>
        <dt className="opacity-60">셔틀</dt>
        <dd>{f.shuttle?.description ?? "미확인"}</dd>
        <dt className="opacity-60">오시는 길</dt>
        <dd>{f.directions?.description ?? "미확인"}</dd>
      </dl>

      <section className="space-y-2">
        <h2 className="font-semibold">프로그램</h2>
        {programs.loading && <Loading />}
        {programs.error && <ErrorBox error={programs.error} />}
        {programs.data?.length === 0 && <p className="text-sm opacity-60">등록된 프로그램이 없어요.</p>}
        <ul className="space-y-1 text-sm">
          {programs.data?.map((p) => (
            <li key={p.id}>
              {p.startAt ? `${p.startAt.slice(5, 10)} ${time(p.startAt)}~${time(p.endAt)}` : "시간 미정"} · {p.name}
            </li>
          ))}
        </ul>
      </section>

      {f.sourceUrl && (
        <p className="text-xs opacity-60">
          출처: <a href={f.sourceUrl} className="underline">{f.sourceUrl}</a>
        </p>
      )}

      {f.status !== "CANCELLED" && (
        <div className="fixed inset-x-0 bottom-14 mx-auto max-w-md px-4 pb-2">
          <Link href={`/festivals/${f.id}/trip`} className={`${button} block text-center`}>
            이 축제로 AI 여행 코스 추천받기
          </Link>
        </div>
      )}
    </div>
  );
}
