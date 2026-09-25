// @vitest-environment node
import { Linter } from "eslint";
import { describe, expect, test } from "vitest";
// eslint.config.js is plain JS with no type declarations; the flat-config
// array shape is exactly what Linter.verify's `config` argument expects.
// @ts-expect-error -- untyped JS config module, see comment above.
import config from "../../eslint.config.js";

/**
 * Positive-control coverage for the no-restricted-syntax selectors in
 * eslint.config.js that ban a local binding named `window` or `document`.
 * Such a binding shadows the DOM global of the same name, so every later
 * reference in that scope silently resolves to the local value — the failure
 * is invisible until something in the scope actually wants the real global.
 * Running the real flat config against violating fixtures means a future edit
 * that defangs a selector fails a test rather than waiting for the next
 * accidental shadow.
 */
describe("the local `window`/`document` shadowing ban actually fires", () => {
  const linter = new Linter({ configType: "flat" });

  function ruleIds(code: string): string[] {
    const messages = linter.verify(code, config, { filename: "src/__fixture__.ts" });
    return messages.map((m) => m.ruleId).filter((id): id is string => id !== null);
  }

  test("a const named window is flagged", () => {
    expect(ruleIds("function f() {\n  const window = { startSec: 0 };\n  return window;\n}\n")).toContain(
      "no-restricted-syntax",
    );
  });

  test("a let named document is flagged", () => {
    expect(ruleIds("function f() {\n  let document = 1;\n  document += 1;\n  return document;\n}\n")).toContain(
      "no-restricted-syntax",
    );
  });

  test("a parameter named window is flagged", () => {
    expect(ruleIds("export function f(window: number) {\n  return window;\n}\n")).toContain("no-restricted-syntax");
  });

  test("an arrow-function parameter named document is flagged", () => {
    expect(ruleIds("const f = (document: number) => document;\n")).toContain("no-restricted-syntax");
  });

  // The forms a positional selector is likeliest to miss: each of these binds
  // the bare name just as hard as `const window = …` does.
  test.each([
    ["a destructured object binding", "function f(o: { window: number }) {\n  const { window } = o;\n  return window;\n}\n"],
    ["a renamed destructured binding", "function f(o: { a: number }) {\n  const { a: document } = o;\n  return document;\n}\n"],
    ["a destructured array binding", "function f(a: number[]) {\n  const [window] = a;\n  return window;\n}\n"],
    ["a rest binding", "function f(a: number[]) {\n  const [, ...document] = a;\n  return document;\n}\n"],
    ["a destructured parameter", "export function f({ window }: { window: number }) {\n  return window;\n}\n"],
    ["a parameter with a default", "export function f(document = 1) {\n  return document;\n}\n"],
    ["a catch-clause parameter", "function f() {\n  try {\n    g();\n  } catch (window) {\n    return window;\n  }\n}\ndeclare function g(): void;\n"],
    ["a named import", "import { window } from './x';\nexport const a = window;\n"],
    ["a default import", "import document from './x';\nexport const a = document;\n"],
    ["a namespace import", "import * as window from './x';\nexport const a = window;\n"],
    ["a function declaration's own name", "export function window() {\n  return 1;\n}\n"],
  ])("%s is flagged", (_label, code) => {
    expect(ruleIds(code)).toContain("no-restricted-syntax");
  });

  test("a descriptive name and a real global reference stay clean", () => {
    const code = "export function f(viewWindow: number) {\n  return viewWindow + window.innerWidth;\n}\n";
    expect(ruleIds(code)).toEqual([]);
  });

  // Nothing here creates a scope where a bare `window`/`document` could
  // resolve to something other than the DOM global, so flagging them would
  // cost a rename and buy no safety.
  test.each([
    ["an object property", "export const o = { window: 1, document: 2 };\n"],
    ["a member expression", "export const a = window.innerWidth + document.title.length;\n"],
    ["an interface method signature", "export interface I {\n  m(window: number): void;\n}\n"],
    ["a function-type alias", "export type Fn = (document: number) => void;\n"],
  ])("%s stays clean", (_label, code) => {
    expect(ruleIds(code)).toEqual([]);
  });
});
