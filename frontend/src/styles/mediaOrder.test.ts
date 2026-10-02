import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// A `max-width` override only works when it comes after the rule it
// overrides: at equal specificity the later rule wins at every width, so a
// phone override written above its base rule silently never applies.

type Rule = { selector: string; media: string | null; props: Set<string> };

function rules(css: string): Rule[] {
  const src = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: Rule[] = [];
  let i = 0;
  function block(media: string | null): void {
    while (i < src.length) {
      const open = src.indexOf("{", i);
      const close = src.indexOf("}", i);
      if (close !== -1 && (open === -1 || close < open)) {
        i = close + 1;
        return;
      }
      if (open === -1) {
        i = src.length;
        return;
      }
      const head = src.slice(i, open).trim();
      i = open + 1;
      if (head.startsWith("@media")) {
        block(head);
      } else if (head.startsWith("@")) {
        let depth = 1;
        while (depth > 0 && i < src.length) {
          if (src[i] === "{") depth += 1;
          else if (src[i] === "}") depth -= 1;
          i += 1;
        }
      } else {
        const end = src.indexOf("}", i);
        const props = new Set([...src.slice(i, end).matchAll(/([\w-]+)\s*:/g)].map((m) => m[1]));
        for (const selector of head.split(",")) out.push({ selector: selector.trim(), media, props });
        i = end + 1;
      }
    }
  }
  block(null);
  return out;
}

function cancelledOverrides(css: string): string[] {
  const all = rules(css);
  return all.flatMap((rule, index) => {
    if (!rule.media?.includes("max-width")) return [];
    return all
      .slice(index + 1)
      .filter((later) => later.media === null && later.selector === rule.selector)
      .flatMap((later) => [...rule.props].filter((p) => later.props.has(p)))
      .map((prop) => `${rule.selector} { ${prop} } in ${rule.media}`);
  });
}

function stylesheets(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return stylesheets(full);
    return e.name.endsWith(".css") ? [full] : [];
  });
}

describe("media query order", () => {
  it("flags a phone override placed above the base rule it overrides", () => {
    const css = `@media (max-width: 640px) { .hero { grid-template-columns: 1fr; } }
      .hero { display: grid; grid-template-columns: 1fr 1fr; }`;
    expect(cancelledOverrides(css)).toEqual([".hero { grid-template-columns } in @media (max-width: 640px)"]);
  });

  it("accepts the override once it follows the base rule", () => {
    const css = `.hero { display: grid; grid-template-columns: 1fr 1fr; }
      @media (max-width: 640px) { .hero { grid-template-columns: 1fr; } }`;
    expect(cancelledOverrides(css)).toEqual([]);
  });

  it("finds no cancelled phone override in any stylesheet", () => {
    const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
    const found = stylesheets(root).flatMap((file) =>
      cancelledOverrides(readFileSync(file, "utf-8")).map((hit) => `${path.relative(root, file)}: ${hit}`),
    );
    expect(found).toEqual([]);
  });
});
