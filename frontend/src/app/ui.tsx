"use client";

import Link from "next/link";
import { useState } from "react";
import type { ApiFailure } from "@/lib/api";
import { FIELDS, REGIONS } from "@/lib/labels";
import type { Festival } from "@/lib/types";

export function Loading() {
  return <p className="py-6 text-center text-sm opacity-60" role="status">불러오는 중…</p>;
}

export function ErrorBox({ error, onRetry }: { error: ApiFailure; onRetry?: () => void }) {
  return (
    <div role="alert" className="my-3 rounded-lg border border-black/15 p-3 text-sm">
      <p>⚠ {error.body.message}</p>
      {!!error.body.fields?.length && (
        <ul className="mt-1 list-disc pl-5">
          {error.body.fields.map((f, i) => <li key={i}>{FIELDS[f.path.split(".")[0]] ?? f.path} 확인 필요</li>)}
        </ul>
      )}
      {onRetry && (
        <button onClick={onRetry} className="mt-2 underline">
          다시 시도
        </button>
      )}
    </div>
  );
}

export function FestivalCard({ f }: { f: Festival }) {
  return (
    <Link href={`/festivals/${f.id}`} className="block rounded-lg border border-black/10 p-3">
      <p className="font-semibold">{f.name}</p>
      <p className="text-sm opacity-70">
        {REGIONS[f.region] ?? f.region} · {f.startDate} ~ {f.endDate}
      </p>
      {f.recommendationReason && <p className="mt-1 text-sm">{f.recommendationReason}</p>}
    </Link>
  );
}

/** 목록 다음 페이지. useApi().more가 있을 때만 보인다. */
export function MoreButton({ onMore }: { onMore?: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  if (!onMore) return null;
  return (
    <button disabled={busy} onClick={() => { setBusy(true); onMore().finally(() => setBusy(false)); }}
      className="w-full rounded-lg border border-black/20 py-2 text-sm disabled:opacity-40">
      {busy ? "불러오는 중…" : "더 보기"}
    </button>
  );
}

export const button = "rounded-lg bg-foreground px-4 py-3 font-semibold text-background disabled:opacity-40";
export const input = "w-full rounded-lg border border-black/20 bg-transparent px-3 py-2";
