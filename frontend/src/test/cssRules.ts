/** Reading declarations back out of a stylesheet, for tests whose subject is
 *  the CSS itself. vitest runs with `css: false`, so a class-driven
 *  declaration can never be read off a rendered node -- the stylesheet source
 *  is the only thing there is to assert against. */

/** Body of the first rule whose selector text starts at `selector`, with
 *  braces balanced so nested at-rules and rules are included. */
export function ruleBody(css: string, selector: string): string {
  const at = css.indexOf(selector);
  if (at === -1) throw new Error(`selector not found: ${selector}`);
  const open = css.indexOf("{", at + selector.length - 1);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}" && --depth === 0) return css.slice(open + 1, i);
  }
  throw new Error(`unbalanced braces after: ${selector}`);
}

/** Last declared value of `prop` in `body` -- later wins, as the cascade has
 *  it -- with runs of whitespace collapsed. */
export function decl(body: string, prop: string): string | null {
  const re = new RegExp(`(?:^|[;{\\s])${prop}\\s*:\\s*([^;]+);`, "g");
  let last: string | null = null;
  for (const m of body.matchAll(re)) last = m[1].replace(/\s+/g, " ").trim();
  return last;
}
