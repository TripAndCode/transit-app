import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDelete, apiGet, apiPatch, apiPost } from "./client";

type AdminUser = {
  user_id: number;
  email: string;
  name: string | null;
  avatar_url: string | null;
  role: "user" | "admin";
  suspended_at: string | null;
  created_at: string;
};

type AdminUserList = { users: AdminUser[]; total: number };

/** Paginated/filterable admin user list (q, role, suspended, limit/offset). */
export function useAdminUsers(params: {
  q?: string;
  role?: string;
  suspended?: string;
  limit?: number;
  offset?: number;
}) {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.role) qs.set("role", params.role);
  if (params.suspended) qs.set("suspended", params.suspended);
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  return useQuery({
    queryKey: ["adminUsers", params],
    queryFn: ({ signal }) => apiGet<AdminUserList>(`/api/admin/users?${qs}`, { signal }),
  });
}

async function patchUser(uid: number, body: { role?: string; suspended?: boolean }) {
  return apiPatch<AdminUser>(`/api/admin/users/${uid}`, body);
}

async function deleteUser(uid: number) {
  await apiDelete(`/api/admin/users/${uid}`);
}

/** Mutation: PATCH a user's role/suspended flag; invalidates the user list and detail queries on success. */
export function usePatchUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ uid, body }: { uid: number; body: { role?: string; suspended?: boolean } }) =>
      patchUser(uid, body),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["adminUsers"] });
      qc.invalidateQueries({ queryKey: ["adminUser", String(variables.uid)] });
    },
  });
}

/** Mutation: soft-delete a user; invalidates the user list and detail queries on success. */
export function useDeleteUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (uid: number) => deleteUser(uid),
    onSuccess: (_data, uid) => {
      qc.invalidateQueries({ queryKey: ["adminUsers"] });
      qc.invalidateQueries({ queryKey: ["adminUser", String(uid)] });
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
