import Link from "next/link";
import { SearchX, CircleAlert, ChevronRight } from "lucide-react";
import { regions, type FestivalSummary } from "@/lib/types";
import { ApiError } from "@/lib/api/client";
export function SectionHeading({
  title,
  href,
  label,
}: {
  title: string;
  href?: string;
  label: string;
}) {
  return (
    <div className="section-heading">
      <h2>{title}</h2>
      {href ? <Link href={href}>{label}</Link> : <span>{label}</span>}
    </div>
  );
}
export function FestivalRow({ festival: f }: { festival: FestivalSummary }) {
  return (
    <Link className="festival-row" href={`/festivals/${f.id}`}>
      <div>
        <h3>
          {f.name}
          {f.status === "CANCELLED" && " · 취소"}
        </h3>
        <p>
          {regions[f.region]} · {formatDates(f.startDate, f.endDate)}
        </p>
      </div>
      <ChevronRight size={16} aria-hidden="true" />
    </Link>
  );
}
export function formatDates(start: string, end: string) {
  return `${start.slice(5).replace("-", ".")}—${end.slice(5).replace("-", ".")}`;
}
export function Loading() {
  return (
    <div
      role="status"
      aria-label="축제 정보를 불러오는 중"
      className="space-y-4"
    >
      <span className="sr-only">불러오는 중…</span>
      {[1, 2, 3].map((n) => (
        <div key={n} className="skeleton h-[74px] rounded-2xl" />
      ))}
    </div>
  );
}
export function ErrorState({
  message,
  retry,
  title = "잠시 쉬어 가는 중이에요",
}: {
  message: string;
  retry?: () => void;
  title?: string;
}) {
  return (
    <div role="alert" className="state-card">
      <CircleAlert aria-hidden="true" />
      <h2>{title}</h2>
      <p>{message}</p>
      {retry && (
        <button type="button" className="button secondary" onClick={retry}>
          다시 시도
        </button>
      )}
    </div>
  );
}
/** 403·404는 다시 시도해도 같으므로 재시도 버튼 없이 안내한다. */
export function ApiErrorState({
  error,
  retry,
}: {
  error: Error;
  retry: () => void;
}) {
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 403)
    return <ErrorState title="볼 수 없는 정보예요" message="이 정보에 접근할 권한이 없어요." />;
  if (status === 404)
    return <ErrorState title="찾을 수 없어요" message="삭제되었거나 주소가 잘못되었어요." />;
  return <ErrorState message={error.message} retry={retry} />;
}
export function EmptyState({ reset }: { reset?: () => void }) {
  return (
    <div className="state-card" role="status">
      <SearchX aria-hidden="true" />
      <h2>아직 만날 축제가 없어요</h2>
      <p>다른 검색어나 조건으로 찾아보세요.</p>
      {reset ? (
        <button className="button secondary" onClick={reset}>
          검색 조건 초기화
        </button>
      ) : (
        <Link className="text-link" href="/festivals">
          전체 축제 찾아보기
        </Link>
      )}
    </div>
  );
}
export function Notice({ children }: { children: React.ReactNode }) {
  return <div className="notice">{children}</div>;
}
