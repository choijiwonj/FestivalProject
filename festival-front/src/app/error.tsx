"use client";
import { useEffect } from "react";
import { ErrorState } from "@/components/ui";
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // 원인 추적용. 수집 서비스가 생기면 여기서 전송한다.
    console.error(error);
  }, [error]);
  return (
    <ErrorState
      message={`화면을 불러오지 못했어요. 다시 시도해 주세요.${error.digest ? ` (오류 번호 ${error.digest})` : ""}`}
      retry={reset}
    />
  );
}
