import { use, useState, type ComponentProps, type ReactNode } from "react";
import { PageHeader } from "../ui/PageHeader";
import { ScopeSlotContext } from "./scopeSlot";
import "./scope.css";

/** Wraps a page whose heading should carry the scope sentence's actions. */
export function ScopeHeaderProvider({ children }: { children: ReactNode }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  return <ScopeSlotContext value={{ slot, setSlot }}>{children}</ScopeSlotContext>;
}

/** A page heading whose row holds the saved views and the pin of the scope
 *  sentence below it. The row stays empty on a view with no sentence, so a
 *  pin never shows without the strip it toggles. */
export function ScopePageHeader(props: Omit<ComponentProps<typeof PageHeader>, "actions" | "className">) {
  const ctx = use(ScopeSlotContext);
  return <PageHeader {...props} className="scope-page-head" actions={<span ref={ctx?.setSlot} className="scope-actions" />} />;
}
