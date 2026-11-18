export type Festival = {
  id: string;
  name: string;
  region: string;
  startDate: string;
  endDate: string;
  thumbnailUrl: string | null;
  tags: string[];
  status: string;
  recommendationReason?: string;
};

export type FestivalDetail = Festival & {
  description: string;
  venue: { label: string; lat: number; lng: number } | null;
  openingHours: string | null;
  admission: { amountKrw: number | null; basis: string | null } | null;
  shuttle: { description: string; url: string } | null;
  directions: { description: string; url: string } | null;
  sourceUrl: string | null;
  lastVerifiedAt: string | null;
  dateSelection: { min: string; max: string };
};

export type Program = { id: string; name: string; startAt: string | null; endAt: string | null; costKrw: number | null };

export type Location = { locationId: string; label: string; lat: number; lng: number };

export type ValidationResult = {
  valid: boolean;
  normalizedInput: Record<string, unknown>;
  days: number;
  nights: number;
  warnings: { code: string; message: string }[];
};

export type Job = {
  id: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
  stage: string;
  result: { planId?: string } | null;
  error: { code: string; message: string; retryable?: boolean } | null;
  festivalId?: string | null;
};

export type Stop = {
  id: string;
  type: string;
  name: string;
  startAt: string | null;
  endAt: string | null;
  costKrw: number | null;
  costStatus: string;
  recommendationReason?: string | null;
  address?: string | null;
};

export type Leg = {
  fromStopId: string; toStopId: string; mode: string;
  durationMinutes: number | null; distanceMeters: number | null;
  departureAt?: string | null; arrivalAt?: string | null;
  serviceTimeStatus?: string;
  segments?: { mode: string; fromName?: string; toName?: string; durationMinutes?: number;
    lines?: { busNo?: string; name?: string }[]; walkabilityStatus?: string }[];
};

export type TripPlan = {
  id: string;
  version: number;
  title: string;
  status: "READY" | "NEEDS_REVIEW" | "INFEASIBLE";
  days: { date: string; stops: Stop[]; legs?: Leg[] }[];
  inputSnapshot: Record<string, unknown> & { origin?: Location; additionalRequests?: string | null };
  parentPlanId?: string | null;
  budget: { limitKrw: number | null; knownTotalKrw: number; unknownItemCount: number; status: string };
  warnings: { code: string; message: string }[];
  conflicts: { code: string; message: string }[];
  createdAt: string;
};

export type PlanExport = {
  id: string;
  planId: string;
  jobId: string;
  planVersion: number;
  format: "PDF" | "PNG";
  dates: string[];
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
  downloadUrl: string | null;
  contentType: string | null;
  filename: string | null;
  error: { code: string; message: string } | null;
};

export type PlanSummary = { id: string; title: string; startDate: string; endDate: string; status: string };

export type UserSettings = {
  version: number;
  travelDefaults: {
    origin: Location | null;
    transport: string | null;
    travelers: number | null;
    interests: string[];
    style: string | null;
    lodgingPreference: string | null;
  };
  privacy: { saveOrigin: boolean };
};
