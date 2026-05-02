export const COLORS = {
  bgPage: "#fafaf8",
  bgSurface: "#ffffff",
  borderSoft: "#eeeeee",
  textPrimary: "#2a2a2a",
  textSecondary: "#6a6a6a",
  accent: "#5b6cad",
  errorBg: "#fdf6e3",
  errorFg: "#8a6f1c",
} as const;

// Calm severity ramp for delays (minutes -> color)
export const DELAY_RAMP = {
  ok: "#8fb88f",       // < 2 min   sage
  mild: "#d4b878",     // 2 – 5 min sand
  moderate: "#c98a5e", // 5 – 10 min terracotta
  severe: "#a85d52",   // > 10 min  brick
} as const;

export function delayColor(minutes: number): string {
  const m = Math.abs(minutes);
  if (m < 2) return DELAY_RAMP.ok;
  if (m < 5) return DELAY_RAMP.mild;
  if (m < 10) return DELAY_RAMP.moderate;
  return DELAY_RAMP.severe;
}
