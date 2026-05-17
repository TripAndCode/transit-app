import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type AdminUser = {
  user_id: number;
  email: string;
  name: string | null;
  avatar_url: string | null;
  role: "user" | "admin";
  suspended_at: string | null;
  created_at: string;
};

export type AdminUserList = { users: AdminUser[]; total: number };

/** Paginated/filterable admin user list (q, role, suspended). */
export function useAdminUsers(params: { q?: string; role?: string; suspended?: string }) {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.role) qs.set("role", params.role);
  if (params.suspended) qs.set("suspended", params.suspended);
  return useQuery({
    queryKey: ["adminUsers", params],
    queryFn: async (): Promise<AdminUserList> => {
      const r = await fetch(`/api/admin/users?${qs}`, { credentials: "include" });
      if (!r.ok) throw new Error(`/api/admin/users ${r.status}`);
      return r.json();
    },
  });
}

async function patchUser(uid: number, body: { role?: string; suspended?: boolean }) {
  const r = await fetch(`/api/admin/users/${uid}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.detail || `PATCH ${r.status}`);
  }
  return r.json();
}

async function deleteUser(uid: number) {
  const r = await fetch(`/api/admin/users/${uid}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!r.ok && r.status !== 204) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.detail || `DELETE ${r.status}`);
  }
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
