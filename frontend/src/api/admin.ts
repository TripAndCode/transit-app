import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDelete, apiGet, apiPatch, apiPost } from "./client";

export type AdminUser = {
  user_id: number;
  email: string;
  name: string | null;
  avatar_url: string | null;
  role: "user" | "admin";
  suspended_at: string | null;
  llm_approved: boolean;
  created_at: string;
};

type AdminUserList = { users: AdminUser[]; total: number };

export type UserPatchBody = { role?: string; suspended?: boolean; llm_approved?: boolean };

/** Paginated/filterable admin user list (q, role, suspended, llmApproved, limit/offset). */
export function useAdminUsers(params: {
  q?: string;
  role?: string;
  suspended?: string;
  llmApproved?: string;
  limit?: number;
  offset?: number;
}) {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.role) qs.set("role", params.role);
  if (params.suspended) qs.set("suspended", params.suspended);
  if (params.llmApproved) qs.set("llm_approved", params.llmApproved);
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  return useQuery({
    queryKey: ["adminUsers", params],
    queryFn: ({ signal }) => apiGet<AdminUserList>(`/api/admin/users?${qs}`, { signal }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

async function patchUser(uid: number, body: UserPatchBody) {
  return apiPatch<AdminUser>(`/api/admin/users/${uid}`, body);
}

async function deleteUser(uid: number) {
  await apiDelete(`/api/admin/users/${uid}`);
}

async function bulkPatchUsers(ids: number[], patch: UserPatchBody) {
  return apiPatch<AdminUser[]>("/api/admin/users/bulk", { ids, patch });
}

/** Mutation: PATCH a user's role/suspended flag; invalidates the user list and detail queries on success. */
export function usePatchUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ uid, body }: { uid: number; body: UserPatchBody }) => patchUser(uid, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["adminUsers"] });
      // Prefix match — at most one detail query is ever mounted, so this is
      // cheap, and it doesn't depend on the detail page's uid key staying a
      // string (unlike reconstructing ["adminUser", String(uid)] here).
      qc.invalidateQueries({ queryKey: ["adminUser"] });
    },
  });
}

/** Mutation: PATCH one patch across many users in a single transaction;
 * invalidates the user list and detail queries on success. The undo flow
 * (AdminUsersPage) re-invokes this with the inverse patch over the same ids. */
export function useBulkPatchUsers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ids, patch }: { ids: number[]; patch: UserPatchBody }) => bulkPatchUsers(ids, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["adminUsers"] });
      qc.invalidateQueries({ queryKey: ["adminUser"] });
    },
  });
}

/** Mutation: soft-delete a user; invalidates the user list and evicts the detail query on success. */
export function useDeleteUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (uid: number) => deleteUser(uid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["adminUsers"] });
      // Evict rather than invalidate: the caller navigates away from the
      // detail page on delete, so refetching the just-deleted row first
      // would only be a wasted request.
      qc.removeQueries({ queryKey: ["adminUser"] });
    },
  });
}

// ── Agency admin types ────────────────────────────────────────────────────

export type AdminAgency = {
  agency_id: number;
  agency_name: string;
  feed_url: string;
  static_url: string | null;
  ingest_strategy: string | null;
  trip_id_pattern: string | null;
  deleted_at: string | null;
};

type AgencyCreate = {
  agency_name: string;
  feed_url: string;
  static_url?: string | null;
  ingest_strategy?: string | null;
  trip_id_pattern?: string | null;
};

type AgencyPatch = Partial<Omit<AgencyCreate, "agency_name"> & { agency_name: string }>;

// ── Agency admin hooks ───────────────────────────────────────────────────

/** Admin list of ALL agencies including soft-deleted. */
export function useAdminAgencies() {
  return useQuery({
    queryKey: ["adminAgencies"],
    queryFn: ({ signal }) => apiGet<AdminAgency[]>("/api/admin/agencies", { signal }),
  });
}

/** Mutation: create an agency via POST /api/agencies. */
export function useCreateAgencyAdmin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AgencyCreate) => apiPost<AdminAgency>("/api/agencies", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["adminAgencies"] });
      qc.invalidateQueries({ queryKey: ["agencies"] });
    },
  });
}

/** Mutation: PATCH an agency. */
export function usePatchAgency() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: AgencyPatch }) =>
      apiPatch<AdminAgency>(`/api/agencies/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["adminAgencies"] });
      qc.invalidateQueries({ queryKey: ["agencies"] });
    },
  });
}

/** Mutation: soft-delete an agency. */
export function useDeleteAgency() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiDelete(`/api/agencies/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["adminAgencies"] });
      qc.invalidateQueries({ queryKey: ["agencies"] });
    },
  });
}

/** Mutation: restore a soft-deleted agency. */
export function useRestoreAgency() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiPost<AdminAgency>(`/api/agencies/${id}/restore`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["adminAgencies"] });
      qc.invalidateQueries({ queryKey: ["agencies"] });
    },
  });
}

// ── Ops health ────────────────────────────────────────────────────────────

export type AgencyFreshnessItem = {
  agency_id: number;
  agency_name: string;
  last_analyzed_at: string | null;
  analyze_age_hours: number | null;
  agg_fresh: boolean;
  agg_behind_days: number;
  is_stale: boolean;
  data_to: string | null;
  clamp_pct: number | null;
};

type OpsHealth = {
  migrations: { applied: string | null; latest: string | null; behind: number } | null;
  agencies: AgencyFreshnessItem[];
  // False only when the agencies sub-check itself failed — distinguishes
  // "checked, zero agencies" from "check failed" (both give an empty array).
  agencies_ok: boolean;
};

export function useAdminOps() {
  return useQuery({
    queryKey: ["adminOps"],
    queryFn: ({ signal }) => apiGet<OpsHealth>("/api/admin/ops", { signal }),
    staleTime: 30_000,
  });
}

// ── Architecture docs (developer/internal page) ─────────────────────────

type ArchitectureDocSummary = {
  slug: string;
  title: string;
};

type ArchitectureDoc = ArchitectureDocSummary & {
  content: string;
};

/** The `/admin/architecture` page's sidebar index of `docs/features/*.md`.
 * Enumerated server-side (fresh glob per request), never hardcoded here. */
export function useArchitectureDocs() {
  return useQuery({
    queryKey: ["adminArchitectureDocs"],
    queryFn: ({ signal }) => apiGet<ArchitectureDocSummary[]>("/api/admin/architecture/docs", { signal }),
    staleTime: 30_000,
  });
}

/** One feature doc's full Markdown content, fetched only once a sidebar
 * entry is selected. `slug === null` (nothing selected yet) disables the
 * query instead of firing a request for a not-yet-known doc. */
export function useArchitectureDoc(slug: string | null) {
  return useQuery({
    queryKey: ["adminArchitectureDoc", slug],
    queryFn: ({ signal }) =>
      apiGet<ArchitectureDoc>(`/api/admin/architecture/docs/${encodeURIComponent(slug ?? "")}`, { signal }),
    enabled: slug != null,
    staleTime: 30_000,
  });
}

// ── Control board ────────────────────────────────────────────────────────

export type BoardCollector = {
  key: string;
  /** Server-side fallback name, used when the UI has no translation for `key`. */
  label: string;
  status: "ok" | "warn" | "down" | "unknown";
  last_success_at: string | null;
  detail: string | null;
  /** 24 hourly cells, oldest first: 1 where the collector was still known good. */
  history: number[];
};

export type BoardFreshnessDay = {
  date: string;
  state: "fresh" | "stale" | "missing";
  clamp_pct: number | null;
};

type BoardFreshnessRow = {
  agency_id: number;
  agency_name: string;
  days: BoardFreshnessDay[];
};

export type BoardAlert = {
  level: "warn" | "info";
  /** Translated as `admin.board.alert.<code>`; `text` is the untranslated
   *  server summary, rendered as-is for a code this build doesn't know. */
  code: string;
  params: Record<string, unknown>;
  text: string;
  href: string | null;
};

export type AdminBoard = {
  collectors: BoardCollector[];
  freshness: BoardFreshnessRow[];
  migrations: { applied: string | null; latest: string | null; behind: number } | null;
  alerts: BoardAlert[];
};

/** The `/admin` entry page's single snapshot. Polled rather than pushed: the
 *  underlying collectors are themselves cached snapshots, so a short poll is
 *  as fresh as the data can be. */
export function useAdminBoard() {
  return useQuery({
    queryKey: ["adminBoard"],
    queryFn: ({ signal }) => apiGet<AdminBoard>("/api/admin/board", { signal }),
    refetchInterval: 10_000,
  });
}

// ── Agency diagnostics (admin agency page) ───────────────────────────────

/** One entry of `rt_field_coverage_probes`. `probed` separates "never
 *  measured" from "measured and refuted"; `expired` marks a verdict past its
 *  TTL, which the read-side gate treats exactly like a missing one. */
export type RtFieldCoverage = {
  present: boolean;
  coverage_pct: number | null;
  sample_size: number | null;
  probed_at: string | null;
  expired: boolean;
  probed: boolean;
};

export type AgencyClampDay = { date: string; clamp_pct: number | null };

type AgencyStaticVersion = {
  version: string;
  loaded_at: string | null;
  trips: number | null;
  vehicle_km: number | null;
  /** Null for a superseded version: the raw static rows a route count needs
   *  are replaced wholesale on every load, so only the current one has them. */
  routes: number | null;
  calendar_until: string | null;
  is_current: boolean;
};

export type AgencyStandard = {
  route_code: string;
  metric_type: string;
  threshold_value: number;
  bonus_malus_rate: number;
};

/** A `route_code` of null is the agency's default weight row. */
export type AgencyWeight = { route_code: string | null; weight: number };

type AgencyDiagnostics = {
  agency_id: number;
  agency_name: string;
  feed_url: string;
  static_url: string | null;
  ingest_strategy: string | null;
  deleted_at: string | null;
  freshness: "fresh" | "stale" | "unknown";
  last_analyzed_at: string | null;
  latest_data_date: string | null;
  last_capture_at: string | null;
  rt_coverage: { fields: Record<string, RtFieldCoverage>; complete: boolean; last_probed_at: string | null };
  static_versions: AgencyStaticVersion[];
  clamp_history: AgencyClampDay[];
  weather_station: { station_id: string; station_name: string; source: string; note: string | null } | null;
  standards: AgencyStandard[];
  standards_count: number;
  weights: AgencyWeight[];
  weights_coverage: { routes_with_weights: number; routes_total: number };
};

export type AgencyHealthRow = {
  agency_id: number;
  agency_name: string;
  feed_url: string;
  ingest_strategy: string | null;
  deleted_at: string | null;
  freshness: "fresh" | "stale" | "unknown";
  latest_data_date: string | null;
  last_analyzed_at: string | null;
  last_capture_at: string | null;
  rt_coverage: {
    complete: boolean;
    present_count: number;
    field_count: number;
    probed: boolean;
    last_probed_at: string | null;
  };
  clamp_history: AgencyClampDay[];
  static_version: { version: string; loaded_at: string | null } | null;
};

/** Health columns for every agency in one request — the list must not cost one
 *  diagnostics call per row. */
export function useAgenciesHealth() {
  return useQuery({
    queryKey: ["adminAgenciesHealth"],
    queryFn: ({ signal }) => apiGet<AgencyHealthRow[]>("/api/admin/agencies/health", { signal }),
    staleTime: 30_000,
  });
}

/** The full diagnostics bundle for one agency, fetched only once its drawer
 *  is open. */
export function useAgencyDiagnostics(agencyId: number | null) {
  return useQuery({
    queryKey: ["adminAgencyDiagnostics", agencyId],
    queryFn: ({ signal }) =>
      apiGet<AgencyDiagnostics>(`/api/admin/agencies/${agencyId}/diagnostics`, { signal }),
    enabled: agencyId != null,
    staleTime: 30_000,
  });
}

type StandardsPatch = { upsert?: AgencyStandard[]; delete?: AgencyStandard[] };
type WeightsPatch = { upsert?: AgencyWeight[]; delete?: AgencyWeight[] };

function invalidateAgencyDiagnostics(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["adminAgencyDiagnostics"] });
  qc.invalidateQueries({ queryKey: ["adminAgenciesHealth"] });
}

/** Mutation: replace/remove `route_performance_standards` rows for an agency. */
export function usePatchAgencyStandards() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: StandardsPatch }) =>
      apiPatch<AgencyStandard[]>(`/api/admin/agencies/${id}/standards`, body),
    onSuccess: () => invalidateAgencyDiagnostics(qc),
  });
}

/** Mutation: replace/remove `ridership_weights` rows for an agency. */
export function usePatchAgencyWeights() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: WeightsPatch }) =>
      apiPatch<AgencyWeight[]>(`/api/admin/agencies/${id}/weights`, body),
    onSuccess: () => invalidateAgencyDiagnostics(qc),
  });
}

/** Mutation: run one live RT field-coverage probe and record the verdict. */
export function useProbeAgencyFeed() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      apiPost<{ status: string; sample_size: number | null; fields: Record<string, boolean> }>(
        `/api/admin/agencies/${id}/probe`,
        {}
      ),
    onSuccess: () => invalidateAgencyDiagnostics(qc),
  });
}

/** Mutation: re-run ingest + analyze for this agency alone, in the background. */
export function useReanalyzeAgency() {
  return useMutation({
    mutationFn: (id: number) => apiPost<{ status: string }>(`/api/admin/agencies/${id}/reanalyze`, {}),
  });
}

// ── User drawer: sessions ─────────────────────────────────────────────────

type AdminSession = {
  sid_prefix: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  user_agent: string | null;
  ip: string | null;
};

/** Active sessions for one user, identified only by a display-safe prefix
 * -- the full session id is a bearer credential and is never fetched. */
export function useUserSessions(uid: number) {
  return useQuery({
    queryKey: ["adminUserSessions", uid],
    queryFn: ({ signal }) => apiGet<AdminSession[]>(`/api/admin/users/${uid}/sessions`, { signal }),
  });
}

/** Mutation: revoke one session by its prefix; refetches the session list. */
export function useRevokeSession(uid: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sidPrefix: string) => apiDelete(`/api/admin/users/${uid}/sessions/${sidPrefix}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["adminUserSessions", uid] }),
  });
}

// ── User drawer: API keys ─────────────────────────────────────────────────

export type AdminApiKey = {
  id: number;
  owner_user_id: number | null;
  tier: string;
  label: string | null;
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
};

export type AdminApiKeyIssued = AdminApiKey & { key: string };

/** API keys issued (via the admin drawer) for one user. */
export function useApiKeys(ownerUserId: number) {
  return useQuery({
    queryKey: ["adminApiKeys", ownerUserId],
    queryFn: ({ signal }) =>
      apiGet<AdminApiKey[]>(`/api/admin/api-keys?owner_user_id=${ownerUserId}`, { signal }),
  });
}

/** Mutation: issue a new API key for a user. The raw key is returned only
 * in this response -- callers must show it once and never refetch it. */
export function useIssueApiKey(ownerUserId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { tier?: string; label?: string | null }) =>
      apiPost<AdminApiKeyIssued>("/api/admin/api-keys", { owner_user_id: ownerUserId, ...body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["adminApiKeys", ownerUserId] }),
  });
}

/** Mutation: revoke an API key by id; refetches the owner's key list. */
export function useRevokeApiKey(ownerUserId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiDelete(`/api/admin/api-keys/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["adminApiKeys", ownerUserId] }),
  });
}

// ── Invites ────────────────────────────────────────────────────────────────

type InviteCreateBody = { email: string; role: "user" | "admin"; llm_approved: boolean };

type AdminInvite = {
  invite_id: number;
  email: string;
  role: "user" | "admin";
  llm_approved: boolean;
  created_at: string;
  expires_at: string;
};

/** Mutation: pre-approve a role (+ optional LLM access) for an email that
 * hasn't signed in yet; the OAuth callback honors it on first login. */
export function useCreateInvite() {
  return useMutation({
    mutationFn: (body: InviteCreateBody) => apiPost<AdminInvite>("/api/admin/invites", body),
  });
}
