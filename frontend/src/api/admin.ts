import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDelete, apiGet, apiPatch } from "./client";

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

/** Paginated/filterable admin user list (q, role, suspended). */
export function useAdminUsers(params: { q?: string; role?: string; suspended?: string }) {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.role) qs.set("role", params.role);
  if (params.suspended) qs.set("suspended", params.suspended);
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

/** Mutation: PATCH a user's role/suspended flag; invalidates the user list on success. */
export function usePatchUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ uid, body }: { uid: number; body: { role?: string; suspended?: boolean } }) =>
      patchUser(uid, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["adminUsers"] }),
  });
}

/** Mutation: soft-delete a user; invalidates the user list on success. */
export function useDeleteUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (uid: number) => deleteUser(uid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["adminUsers"] }),
  });
}
