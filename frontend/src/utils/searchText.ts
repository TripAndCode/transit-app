/** `text` as every search box compares it: NFKC-folded, so full-width digits and
 *  letters from an IME and half-width katakana match their plain forms, then
 *  trimmed and lower-cased. Apply it to both the query and what it is matched
 *  against. */
export function searchKey(text: string): string {
  return text.normalize("NFKC").trim().toLocaleLowerCase();
}
