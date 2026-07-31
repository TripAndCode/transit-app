export type PageItem = number | "ellipsis";

/** Numbered-pager items for `page` of `totalPages`: all pages when the count
 *  is small, otherwise first/last plus a fixed 3-wide window slid (never
 *  clamped per-element, which used to collapse the window at the edges). */
export function pageItems(page: number, totalPages: number): PageItem[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  const start = Math.min(Math.max(2, page - 1), totalPages - 3);
  const items: PageItem[] = [1];
  if (page > 3) items.push("ellipsis");
  items.push(start, start + 1, start + 2);
  if (page < totalPages - 2) items.push("ellipsis");
  items.push(totalPages);
  return items;
}
