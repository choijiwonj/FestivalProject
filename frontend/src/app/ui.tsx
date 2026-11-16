import Link from "next/link";
import type { ApiFailure } from "@/lib/api";
import { REGIONS } from "@/lib/labels";
import type { Festival } from "@/lib/types";

export function Loading() {
  return <p className="py-6 text-center text-sm opacity-60" role="status">불러오는 중…</p>;
}

export function ErrorBox({ error, onRetry }: { error: ApiFailure; onRetry?: () => void }) {
  return (
    <div role="alert" className="my-3 rounded-lg border border-black/15 p-3 text-sm">
      <p>⚠ {error.body.message}</p>
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

export const button = "rounded-lg bg-foreground px-4 py-3 font-semibold text-background disabled:opacity-40";
export const input = "w-full rounded-lg border border-black/20 bg-transparent px-3 py-2";
