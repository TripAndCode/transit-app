import { useSession } from "../../api/auth";

export type CtaKind = "session" | "guest";

/** The one CTA decision, shared by the header button and the hero's primary
 *  CTA. Both kinds go to the dashboard, which every visitor reaches; a
 *  signed-in visitor is told to open it, anyone else that it needs no
 *  sign-in. */
export function useCtaKind(): { kind: CtaKind } {
  const { data: session } = useSession();
  return { kind: session ? "session" : "guest" };
}
