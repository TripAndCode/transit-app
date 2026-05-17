import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

export type Identity = { provider: "google" | "github"; email_at_link: string | null };

export type Session = {
  user_id: number;
  email: string;
  name: string | null;
  avatar_url: string | null;
  role: "user" | "admin";
  identities: Identity[];
};

/** GET /api/me; returns null on 401 so callers can treat anonymous as a normal state. */
async function fetchMe(): Promise<Session | null> {
  const r = await fetch("/api/me", { credentials: "include" });
  if (r.status === 401) return null;
  if (!r.ok) throw new Error(`/api/me ${r.status}`);
  return (await r.json()) as Session;
}

/** React Query hook for the current session (or null when anonymous). */
export function useSession() {
  return useQuery({ queryKey: ["me"], queryFn: fetchMe, staleTime: 30_000 });
}

/** Mutation that posts /api/auth/logout and invalidates the cached session. */
export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/auth/logout", {
        method: "POST",
        credentials: "include",
      });
      if (!r.ok && r.status !== 204) throw new Error("logout failed");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }),
  });
}

/** Build the OAuth start URL for ``provider``, preserving the current path as ``next``. */
export function loginUrl(provider: "google" | "github", next: string = window.location.pathname) {
  return `/api/auth/${provider}/login?next=${encodeURIComponent(next)}`;
}
