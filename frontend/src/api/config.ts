import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";

type AppConfig = { auth_enabled: boolean };

/** Public client config from ``GET /api/config``. Used to hide login UI when
 *  SSO is unconfigured on the backend. Falls back to ``auth_enabled: false``
 *  on network/HTTP error so a broken /api/config never produces dead login links. */
export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: async ({ signal }): Promise<AppConfig> => {
      try {
        return await apiGet<AppConfig>("/api/config", { signal });
      } catch {
        return { auth_enabled: false };
      }
    },
    staleTime: Infinity,
  });
}
