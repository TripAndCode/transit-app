import { useSession } from "../../api/auth";
import { useConfig } from "../../api/config";

export type CtaKind = "session" | "guest";

/** The one CTA decision, shared by the header button and the hero's primary
 *  CTA. Both kinds go to the dashboard, which every visitor reaches; a
 *  signed-in visitor is told to open it, anyone else that it needs no
 *  sign-in. `offerSignIn` is true only once the session is known to be
 *  absent and the deployment has a sign-in method, so the sign-in note never
 *  flashes for a visitor whose session is still loading. */
export function useCtaKind(): { kind: CtaKind; offerSignIn: boolean } {
  const { data: session } = useSession();
  const { data: config } = useConfig();
  const offerSignIn = session === null && (config?.auth_enabled ?? false);
  return { kind: session ? "session" : "guest", offerSignIn };
}
