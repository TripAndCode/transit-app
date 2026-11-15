import { useSession } from "../../api/auth";
import { useConfig } from "../../api/config";

export type CtaKind = "session" | "guest" | "signed_out";

/** The one CTA decision, shared by the header button and the hero's primary
 *  CTA: a signed-in visitor goes to the dashboard; a signed-out visitor on a
 *  deployment that allows guests also goes to the dashboard, with a sign-in
 *  note; anyone else goes to sign-in. While either session or config is
 *  still loading, this reads as `signed_out` (sign-in is always a safe
 *  default -- it never offers a destination the visitor cannot reach). */
export function useCtaKind(): { kind: CtaKind; authEnabled: boolean } {
  const { data: session } = useSession();
  const { data: config } = useConfig();
  const guest = session === null && config != null && !config.login_required;
  const authEnabled = config?.auth_enabled ?? false;
  if (session) return { kind: "session", authEnabled };
  if (guest) return { kind: "guest", authEnabled };
  return { kind: "signed_out", authEnabled };
}
