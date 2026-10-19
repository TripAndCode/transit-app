/** `text` as every client-side search box compares it: NFKC-folded, so
 *  full-width digits and letters from an IME and half-width katakana match their
 *  plain forms, then trimmed and lower-cased. The lower-casing ignores the
 *  runtime locale, under which a Turkish "I" would fold to a dotless "ı". Apply
 *  it to both the query and what it is matched against. */
export function searchKey(text: string): string {
  return text.normalize("NFKC").trim().toLowerCase();
}
