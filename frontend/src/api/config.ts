import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";

export type AppConfig = { auth_enabled: boolean };

/** Public client config from ``GET /api/config``. Used to hide login UI when
 *  SSO is unconfigured on the backend. Falls back to ``auth_enabled: false``
 *  on network/HTTP error so a broken /api/config never produces dead login links. */
export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: async (): Promise<AppConfig> => {
      try {
        return await apiGet<AppConfig>("/api/config");
      } catch {
        return { auth_enabled: false };
      }
    },
    staleTime: Infinity,
  });
}
