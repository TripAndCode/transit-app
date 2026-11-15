/** True for a `#rrggbb` hex color string, case-insensitive. */
export const isHexColor = (value: string): boolean => /^#[0-9a-f]{6}$/i.test(value);
