import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, extname, relative, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// Same resolution styles/zIndex.test.ts uses to read global.css.
const SRC = dirname(dirname(fileURLToPath(import.meta.url)));

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if ([".ts", ".tsx"].includes(extname(name)) && !name.includes(".test.")) out.push(full);
  }
  return out;
}

/**
 * React Query hands every `queryFn` an `AbortSignal` and aborts it when the
 * query is cancelled — a tab switch, an unmount, a superseding key. A
 * `queryFn` that ignores it leaves the request running: the response is
 * discarded, but the round trip is still paid for, and for a billed
 * endpoint that is real money.
 *
 * Asserted across the tree rather than at the fetch helper, because the
 * helper already accepts a signal. What goes wrong is a call site that
 * doesn't pass one, and those are only findable by enumerating them.
 */
describe("every queryFn forwards its abort signal", () => {
  it("has no queryFn that drops the signal argument", () => {
    const offenders: string[] = [];
    for (const file of walk(SRC)) {
      const lines = readFileSync(file, "utf8").split("\n");
      lines.forEach((line, i) => {
        if (!line.includes("queryFn:")) return;
        // The signal may be destructured on this line or, for a multi-line
        // body, on the same one as the arrow — both start `({ signal }`.
        if (line.includes("signal")) return;
        offenders.push(`${relative(SRC, file)}:${i + 1}: ${line.trim()}`);
      });
    }
    expect(offenders, "these queryFns ignore the abort signal").toEqual([]);
  });
});
