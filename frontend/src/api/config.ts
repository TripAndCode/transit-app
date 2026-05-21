import { useQuery } from "@tanstack/react-query";

export type AppConfig = { auth_enabled: boolean };

/** Public client config from ``GET /api/config``. Used to hide login UI when
 *  SSO is unconfigured on the backend. Falls back to ``auth_enabled: false``
 *  on network error so a broken /api/config never produces dead login links. */
export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: async (): Promise<AppConfig> => {
      const r = await fetch("/api/config", { credentials: "include" });
      if (!r.ok) return { auth_enabled: false };
      return (await r.json()) as AppConfig;
    },
    staleTime: Infinity,
  });
}
