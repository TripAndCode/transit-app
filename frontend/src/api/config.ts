import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";

type AppConfig = {
  auth_enabled: boolean;
  local_admin_enabled: boolean;
  login_required?: boolean;
  feedback_url?: string | null;
  llm_enabled?: boolean;
  weather_enabled?: boolean;
};

/** Public client config from ``GET /api/config``. ``auth_enabled`` hides the
 *  Google/GitHub buttons when SSO is unconfigured; ``local_admin_enabled``
 *  separately shows/hides the break-glass username/password form — the two
 *  are independent (a deployment can have either, both, or neither).
 *  Lets a network/HTTP error surface as `isError` rather than swallowing it
 *  into a fake "both false" success, which would hide every sign-in
 *  method behind an infinite staleTime with no way back short of a reload,
 *  indistinguishable from a deployment that genuinely has no SSO configured.
 *  `LoginPage` renders a retry fallback on `isError` instead.
 *  `login_required` says whether the API currently refuses signed-out
 *  callers; a backend that does not send it has no login gate, so its
 *  absence reads as off. `feedback_url` is the deployment's feedback address
 *  (https or mailto), or null when it names none. `llm_enabled` is false
 *  when no LLM provider is usable (or the operator switched LLM answers off),
 *  so LLM-only affordances hide rather than offer something that cannot
 *  answer; absence reads as off. `weather_enabled` mirrors the server flag a
 *  screen needs before it fetches: until the config has arrived, or when a
 *  backend does not send it, it reads as off, so a flag-off feature neither
 *  mounts nor fetches. The config is cached for the session (re-read when
 *  auth state changes), so a flag flipped meanwhile applies on the next read. */
export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: ({ signal }) => apiGet<AppConfig>("/api/config", { signal }),
    staleTime: Infinity,
  });
}

/** Whether a UI-gating server flag is on, for deciding to mount a component. */
export function useFeatureOn(flag: "weather_enabled" | "llm_enabled"): boolean {
  return useConfig().data?.[flag] === true;
}
