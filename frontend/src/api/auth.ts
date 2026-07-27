import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGetOrNull, apiPost } from "./client";

type Identity = { provider: "google" | "github"; email_at_link: string | null };

type Session = {
  user_id: number;
  email: string;
  name: string | null;
  avatar_url: string | null;
  role: "user" | "admin";
  identities: Identity[];
};

/** GET /api/me; returns null on 401 so callers can treat anonymous as a normal state. */
async function fetchMe(): Promise<Session | null> {
  return apiGetOrNull<Session>("/api/me");
}

/** React Query hook for the current session (or null when anonymous). */
export function useSession() {
  return useQuery({ queryKey: ["me"], queryFn: fetchMe, staleTime: 30_000 });
}

/** Mutation that posts /api/auth/logout and clears every cached query.
 *
 * Clears the whole cache, not just ["me"] — every session-scoped query
 * (admin users/agencies/ops, conversations, ...) must stop showing stale
 * data once the session is gone, regardless of whether the caller also does
 * a full page reload (AccountPage does; a future caller might not).
 */
export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost<void>("/api/auth/logout", {}),
    onSuccess: () => qc.clear(),
  });
}

/** Build the OAuth start URL for ``provider``, preserving the current path as ``next``. */
export function loginUrl(provider: "google" | "github", next: string = window.location.pathname) {
  return `/api/auth/${provider}/login?next=${encodeURIComponent(next)}`;
}
