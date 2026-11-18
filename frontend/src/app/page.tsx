"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import type { Festival } from "@/lib/types";
import { ErrorBox, FestivalCard, Loading, input } from "./ui";

// S01 홈 + S02 전체 축제 + S15 결과 없음. 홈에는 AI 생성 버튼을 두지 않는다(B01).
export default function Home() {
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const recs = useApi<Festival[]>("/festivals/recommendations?limit=3");
  const list = useApi<Festival[]>(`/festivals?limit=20${q ? `&q=${encodeURIComponent(q)}` : ""}`);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">축제잇길</h1>

      <section aria-labelledby="recs" className="space-y-2">
        <h2 id="recs" className="font-semibold">추천 축제</h2>
        {recs.loading && <Loading />}
        {/* 추천이 실패하거나 비어도 전체 목록은 그대로 쓸 수 있다 */}
        {recs.data?.length === 0 && <p className="text-sm opacity-60">지금 추천할 축제가 없어요.</p>}
        {recs.data?.map((f) => <FestivalCard key={f.id} f={f} />)}
      </section>

      <section aria-labelledby="all" className="space-y-2">
        <h2 id="all" className="font-semibold">전체 축제</h2>
        <form
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            setQ(draft.trim());
          }}
          className="flex gap-2"
        >
          <label htmlFor="q" className="sr-only">축제명 또는 지역</label>
          <input id="q" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="축제명 또는 지역" className={input} />
          <button className="shrink-0 rounded-lg border border-black/20 px-3">검색</button>
        </form>
        {list.loading && <Loading />}
        {list.error && <ErrorBox error={list.error} />}
        {list.data?.length === 0 && <p className="py-4 text-center text-sm opacity-60">검색 결과가 없어요. 검색어를 바꿔 보세요.</p>}
        {list.data?.map((f) => <FestivalCard key={f.id} f={f} />)}
      </section>
    </div>
  );
}
