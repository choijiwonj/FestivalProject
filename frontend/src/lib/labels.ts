export const REGIONS: Record<string, string> = {
  CHEONAN: "천안", GONGJU: "공주", BORYEONG: "보령", ASAN: "아산", SEOSAN: "서산", NONSAN: "논산",
  GYERYONG: "계룡", DANGJIN: "당진", GEUMSAN: "금산", BUYEO: "부여", SEOCHEON: "서천",
  CHEONGYANG: "청양", HONGSEONG: "홍성", YESAN: "예산", TAEAN: "태안",
};

export const INTERESTS: Record<string, string> = {
  FOOD: "맛집", CAFE: "카페", NATURE: "자연", EXPERIENCE: "체험", CULTURE: "문화", SHOPPING: "쇼핑",
};

export const LODGING: Record<string, string> = {
  ANY: "상관없음", HOTEL: "호텔", PENSION: "펜션", GUESTHOUSE: "게스트하우스", SELF_ARRANGED: "직접 준비",
};

export const STAGES: Record<string, string> = {
  VALIDATING: "조건 확인 중", SEARCHING: "후보 장소 찾는 중", RECOMMENDING: "장소 고르는 중",
  ROUTING: "이동 경로 확인 중", SCHEDULING: "일정 짜는 중", SAVING: "저장 중",
};

export const PLAN_STATUS: Record<string, string> = {
  READY: "바로 실행 가능", NEEDS_REVIEW: "확인 필요", INFEASIBLE: "조건 충돌",
};

export const BUDGET_STATUS: Record<string, string> = {
  NOT_SET: "예산 미입력", WITHIN: "예산 안", OVER: "예산 초과", UNKNOWN: "미확인 비용 있음",
};

export function time(iso: string | null) {
  return iso ? iso.slice(11, 16) : "시간 미정";
}

export function krw(n: number | null) {
  return n === null ? "미확인" : n === 0 ? "무료" : `${n.toLocaleString("ko-KR")}원`;
}
