import type { LucideIcon } from "lucide-react";

/** A question template's line icon, sized to sit before its label text. */
export function CardTemplateIcon({ icon: Icon }: { icon: LucideIcon }) {
  return <Icon size={14} strokeWidth={1.75} aria-hidden="true" style={{ verticalAlign: "-2px" }} />;
}
