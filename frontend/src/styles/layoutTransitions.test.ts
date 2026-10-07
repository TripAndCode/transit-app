import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// Motion grammar: hover and state changes animate colour, opacity or
// transform. A transition on a layout property reflows every frame it runs,
// which is the one cost no token can make cheap. Checked over the source
// tree because the violations arrive one inline style at a time.

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

// `transition`, `transition-property` and `transitionProperty`, in CSS or in
// an inline style object; the value runs to the declaration's end or its
// closing quote, across lines.
const DECLARATION = /\btransition(?:-property|Property)?\s*:\s*["'`]?([^;"'`}]*)/g;
const LAYOUT = new Set(["all", "width", "height", "top", "left", "right", "bottom", "gap", "flex-basis"]);
const LAYOUT_PREFIX = /^(min-|max-)?(width|height)$|^(padding|margin|inset|grid-template)(-[a-z]+)?$/;

/** The value's entries, split at commas outside parentheses so a
 *  cubic-bezier() or var() fallback stays inside its entry. */
function entries(value: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < value.length; i++) {
    if (value[i] === "(") depth++;
    else if (value[i] === ")") depth--;
    else if (value[i] === "," && depth === 0) {
      out.push(value.slice(start, i));
      start = i + 1;
    }
  }
  out.push(value.slice(start));
  return out;
}

/** The layout properties `source` names as a transitioned property. */
function layoutTransitions(source: string): string[] {
  const found: string[] = [];
  for (const m of source.matchAll(DECLARATION)) {
    for (const entry of entries(m[1])) {
      const property = entry.trim().split(/\s+/)[0] ?? "";
      if (LAYOUT.has(property) || LAYOUT_PREFIX.test(property)) found.push(property);
    }
  }
  return found;
}

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    return e.isDirectory() ? walk(full) : [full];
  });
}

describe("no transition animates layout", () => {
  it("names only colour, opacity, transform, filter or shadow in every transition under src", () => {
    const offenders: string[] = [];
    for (const file of walk(root)) {
      if (!/\.(css|tsx?)$/.test(file) || /\.test\.tsx?$/.test(file)) continue;
      for (const property of layoutTransitions(readFileSync(file, "utf8"))) {
        offenders.push(`${path.relative(root, file)}: ${property}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("reads every spelling of a transitioned property", () => {
    expect(layoutTransitions("a { transition: width 200ms ease; }")).toEqual(["width"]);
    expect(layoutTransitions("a { transition-property: max-height; }")).toEqual(["max-height"]);
    expect(layoutTransitions('style={{ transitionProperty: "padding-left" }}')).toEqual(["padding-left"]);
    expect(layoutTransitions('style={{ transition: "all 120ms" }}')).toEqual(["all"]);
    expect(layoutTransitions("a {\n  transition:\n    opacity 1s,\n    left 1s;\n}")).toEqual(["left"]);
  });

  it("leaves paint-only properties alone, however their names read", () => {
    expect(layoutTransitions("a { transition: border-top-color 200ms ease, box-shadow 1s; }")).toEqual([]);
    expect(layoutTransitions("a { transition: transform 200ms cubic-bezier(0.2, 0, 0, 1), opacity 1s; }")).toEqual([]);
    expect(layoutTransitions("a { transition: fill-opacity var(--dur-2, 200ms) var(--ease-out); }")).toEqual([]);
  });
});
