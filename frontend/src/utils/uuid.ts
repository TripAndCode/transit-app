/** Client-generated id for a locally-created record (a saved analysis, an
 *  anonymous conversation) that has no server-assigned id yet.
 *  `crypto.randomUUID` requires a secure context; the fallback covers a
 *  plain-HTTP dev server or an older browser. */
export function uuid(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}
