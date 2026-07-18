import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";

type AppConfig = { auth_enabled: boolean; local_admin_enabled: boolean };

/** Public client config from ``GET /api/config``. ``auth_enabled`` hides the
 *  Google/GitHub buttons when SSO is unconfigured; ``local_admin_enabled``
 *  separately shows/hides the break-glass username/password form — the two
 *  are independent (a deployment can have either, both, or neither).
 *  Falls back to both false on network/HTTP error so a broken /api/config
 *  never produces dead login links. */
export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: async ({ signal }): Promise<AppConfig> => {
      try {
        return await apiGet<AppConfig>("/api/config", { signal });
      } catch {
        return { auth_enabled: false, local_admin_enabled: false };
      }
    },
    staleTime: Infinity,
  });
}
