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
  error: { code: string; message: string } | null;
};

export type Stop = {
  id: string;
  type: string;
  name: string;
  startAt: string | null;
  endAt: string | null;
  costKrw: number | null;
  costStatus: string;
};

export type TripPlan = {
  id: string;
  title: string;
  status: "READY" | "NEEDS_REVIEW" | "INFEASIBLE";
  days: { date: string; stops: Stop[] }[];
  budget: { limitKrw: number | null; knownTotalKrw: number; unknownItemCount: number; status: string };
  warnings: { code: string; message: string }[];
  conflicts: { code: string; message: string }[];
  createdAt: string;
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
