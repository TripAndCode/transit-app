import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";

type AppConfig = { auth_enabled: boolean; local_admin_enabled: boolean };

/** Public client config from ``GET /api/config``. ``auth_enabled`` hides the
 *  Google/GitHub buttons when SSO is unconfigured; ``local_admin_enabled``
 *  separately shows/hides the break-glass username/password form — the two
 *  are independent (a deployment can have either, both, or neither).
 *  Lets a network/HTTP error surface as `isError` rather than swallowing it
 *  into a fake "both false" success -- that used to hide every sign-in
 *  method behind an infinite staleTime with no way back short of a reload,
 *  indistinguishable from a deployment that genuinely has no SSO configured.
 *  `LoginPage` renders a retry fallback on `isError` instead. */
export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: ({ signal }) => apiGet<AppConfig>("/api/config", { signal }),
    staleTime: Infinity,
  });
}
