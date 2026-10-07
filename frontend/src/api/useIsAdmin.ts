import { useSession } from "./auth";
import { useConfig } from "./config";

/** Whether the signed-in user is an admin. Without sign-in configured there
 *  is no admin, whatever a stale session says. */
export function useIsAdmin(): boolean {
  const { data: config } = useConfig();
  const { data: session } = useSession();
  return Boolean(config?.auth_enabled && session?.role === "admin");
}
