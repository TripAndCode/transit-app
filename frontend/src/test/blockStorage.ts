import { vi } from "vitest";

/** Make every call on `localStorage` or `sessionStorage` throw, as a browser
 *  that blocks site data does. vitest.config.ts unstubs globals after each test. */
export function blockStorage(name: "localStorage" | "sessionStorage"): void {
  const blocked = () => {
    throw new DOMException("The operation is insecure.", "SecurityError");
  };
  vi.stubGlobal(name, { getItem: blocked, setItem: blocked, removeItem: blocked, clear: blocked, key: blocked, length: 0 });
}
