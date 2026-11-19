import { Suspense } from "react";
import { FestivalList } from "@/components/festival-list";
import { Loading } from "@/components/ui";
export const metadata = { title: "전체 축제" };
export default function Page() {
  return (
    <Suspense fallback={<Loading label="축제 정보를 불러오는 중" />}>
      <FestivalList />
    </Suspense>
  );
}
