// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { ruleBody, decl } from "../test/cssRules";

const root = path.resolve(process.cwd(), "src");
const css = readFileSync(path.join(root, "styles", "global.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const rootBlock = ruleBody(css, ":root {");

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    return e.isDirectory() ? walk(full) : [full];
  });
}

describe("control tokens", () => {
  it("declares the shared control sizing and easing tokens", () => {
    expect(decl(rootBlock, "--control-h")).toBe("40px");
    expect(decl(rootBlock, "--control-h-sm")).toBe("32px");
    expect(decl(rootBlock, "--control-radius")).toBe("var(--radius-lg)");
    expect(decl(rootBlock, "--control-radius-sm")).toBe("var(--radius)");
    expect(decl(rootBlock, "--ease-spring")).toBe("cubic-bezier(.34, 1.56, .64, 1)");
  });

  it("raises the control heights for phone tap targets at BP.sm", () => {
    const phone = ruleBody(ruleBody(css, "@media (max-width: 640px) {"), ":root {");
    expect(decl(phone, "--control-h")).toBe("44px");
    expect(decl(phone, "--control-h-sm")).toBe("36px");
  });
});

describe(".btn base", () => {
  const base = ruleBody(css, ".btn {");

  it("sizes and shapes from the control tokens", () => {
    expect(decl(base, "display")).toBe("inline-flex");
    expect(decl(base, "height")).toBe("var(--control-h)");
    expect(decl(base, "border-radius")).toBe("var(--control-radius)");
    expect(decl(base, "border")).toBe("0");
    expect(decl(base, "gap")).toBe("var(--space-2)");
    expect(decl(base, "text-decoration")).toBe("none");
  });

  it("transitions transform through --ease-spring and colour/shadow through the --dur-* scale", () => {
    const transition = decl(base, "transition");
    expect(transition).toMatch(/transform\s+var\(--dur-\d\)\s+var\(--ease-spring\)/);
    expect(transition).toMatch(/background-color\s+var\(--dur-\d\)/);
    expect(transition).toMatch(/box-shadow\s+var\(--dur-\d\)/);
    expect(transition).toMatch(/color\s+var\(--dur-\d\)/);
  });

  it("springs down on press", () => {
    expect(decl(ruleBody(css, ".btn:active {"), "transform")).toBe("scale(.96)");
  });

  it("mutes disabled and aria-disabled state with no transform or shadow", () => {
    const disabled = ruleBody(css, ".btn:disabled,\n.btn[aria-disabled=\"true\"] {");
    expect(decl(disabled, "transform")).toBe("none");
    expect(decl(disabled, "cursor")).toBe("not-allowed");
    expect(decl(disabled, "background")).toBe("var(--bg-soft)");
    expect(decl(disabled, "color")).toBe("var(--text-tertiary)");
    expect(decl(disabled, "box-shadow")).toBe("none");
  });

  it("declares :active after every variant's :hover and :disabled after :active, so equal-specificity ties resolve press over hover and disabled over both", () => {
    const activeAt = css.indexOf(".btn:active {");
    const disabledAt = css.indexOf('.btn:disabled,\n.btn[aria-disabled="true"] {');
    expect(activeAt).toBeGreaterThan(-1);
    expect(disabledAt).toBeGreaterThan(activeAt);
    for (const hoverSelector of [".btn--primary:hover {", ".btn--secondary:hover {", ".btn--ghost:hover {", ".btn--icon:hover {"]) {
      const hoverAt = css.indexOf(hoverSelector);
      expect(hoverAt).toBeGreaterThan(-1);
      expect(activeAt).toBeGreaterThan(hoverAt);
    }
  });
});

describe(".btn variants", () => {
  it("--primary is accent-filled with a subtle shadow, stronger and lifted on hover", () => {
    const primary = ruleBody(css, ".btn--primary {");
    expect(decl(primary, "background")).toBe("var(--accent)");
    expect(decl(primary, "color")).toBe("var(--on-accent)");
    expect(decl(primary, "box-shadow")).toBe("var(--el-1)");
    const hover = ruleBody(css, ".btn--primary:hover {");
    expect(decl(hover, "box-shadow")).toBe("var(--el-2)");
    expect(decl(hover, "transform")).toBe("translateY(-1px)");
  });

  it("--secondary is a surface with an inset ring that darkens on hover", () => {
    const secondary = ruleBody(css, ".btn--secondary {");
    expect(decl(secondary, "background")).toBe("var(--bg-surface)");
    expect(decl(secondary, "color")).toBe("var(--text-primary)");
    expect(decl(secondary, "box-shadow")).toBe("inset 0 0 0 1px var(--card-border), var(--el-1)");
    const hover = ruleBody(css, ".btn--secondary:hover {");
    expect(decl(hover, "box-shadow")).toBe("inset 0 0 0 1px var(--text-tertiary), var(--el-1)");
  });

  it("--ghost is transparent with accent text, soft on hover", () => {
    const ghost = ruleBody(css, ".btn--ghost {");
    expect(decl(ghost, "background")).toBe("transparent");
    expect(decl(ghost, "color")).toBe("var(--accent-strong)");
    expect(decl(ruleBody(css, ".btn--ghost:hover {"), "background")).toBe("var(--accent-soft)");
  });

  it("--icon is a square soft tile, darker on hover, and shrinks with --sm", () => {
    const icon = ruleBody(css, ".btn--icon {");
    expect(decl(icon, "width")).toBe("var(--control-h)");
    expect(decl(icon, "height")).toBe("var(--control-h)");
    expect(decl(icon, "padding")).toBe("0");
    expect(decl(icon, "background")).toBe("var(--bg-soft)");
    const hover = ruleBody(css, ".btn--icon:hover {");
    expect(decl(hover, "background")).toBe("var(--border-subtle)");
    expect(decl(hover, "color")).toBe("var(--text-primary)");
    const iconSm = ruleBody(css, ".btn--icon.btn--sm {");
    expect(decl(iconSm, "width")).toBe("var(--control-h-sm)");
    expect(decl(iconSm, "padding")).toBe("0");
  });

  it("--sm shrinks height, padding, radius and type for compact in-card actions", () => {
    // Anchored on the leading newline so this doesn't match inside the
    // ".btn--icon.btn--sm {" compound selector just above it.
    const sm = ruleBody(css, "\n.btn--sm {");
    expect(decl(sm, "height")).toBe("var(--control-h-sm)");
    expect(decl(sm, "border-radius")).toBe("var(--control-radius-sm)");
    expect(decl(sm, "font-size")).toBe("var(--text-sm)");
  });
});

describe("companion classes", () => {
  it("leave the landing CTA's transition to the family, since its lazily loaded sheet wins equal-specificity ties", () => {
    const landing = readFileSync(path.join(root, "pages", "LandingPage.css"), "utf8");
    expect(decl(ruleBody(landing, ".landing-hero__cta {"), "transition")).toBeNull();
  });
});

describe(".btn-ghost retirement", () => {
  it("is gone from every stylesheet and every component", () => {
    const files = walk(root).filter((f) => /\.(css|tsx?)$/.test(f) && !f.endsWith("buttonFamily.test.ts"));
    const offenders = files.filter((f) => readFileSync(f, "utf8").includes("btn-ghost")).map((f) => path.relative(root, f));
    expect(offenders).toEqual([]);
  });
});
