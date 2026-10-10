import { useEffect, useState } from "react";

/**
 * The current time, read again every `intervalMs` for as long as the
 * component is mounted.
 *
 * A `new Date()` taken during render is a value the React Compiler may cache
 * for the component's whole life, so a page meant to stay open (a clock
 * marker, a running bar, a day boundary) has to hold its clock in state that
 * something advances.
 */
export function useNow(intervalMs: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
