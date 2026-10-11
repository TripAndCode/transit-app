import { useSession } from "./auth";
import { useConfig } from "./config";

/** Whether the signed-in user is an admin. Without any sign-in method
 *  configured (SSO or the local break-glass login) there is no admin,
 *  whatever a stale session says. */
export function useIsAdmin(): boolean {
  const { data: config } = useConfig();
  const { data: session } = useSession();
  return Boolean((config?.auth_enabled || config?.local_admin_enabled) && session?.role === "admin");
}
