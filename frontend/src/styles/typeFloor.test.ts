import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

// CJK glyphs need more vertical room than Latin to stay legible: below 12px
// Japanese labels lose stroke separation entirely. 12px (`--text-xs`) is the
// floor for any text rendered as DOM content.
//
// Scope note: this checks the *declaration* forms that style DOM text --
// CSS `font-size:` and React style-object `fontSize:`. The JSX *attribute*
// form (`<text fontSize="10">`) is SVG-only -- chart tick labels, which are
// short numeric/latin axis marks drawn into a viewBox-scaled canvas and are
// deliberately exempt.
const MIN_PX = 12;

// `process.cwd()` is the `frontend/` package root under vitest; the module
// URL is not a file: URL after vite's transform, so it cannot be used here.
const root = path.resolve(process.cwd(), "src");

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return walk(full);
    if (/\.(css|tsx)$/.test(e.name) && !/\.test\.tsx$/.test(e.name)) return [full];
    return [];
  });
}

const PATTERNS = [
  /font-size:\s*([\d.]+)px/g,
  /fontSize:\s*"?([\d.]+)(?:px)?"?\s*[,}]/g,
];

describe("CJK type floor", () => {
  it(`declares no DOM font size below ${MIN_PX}px`, () => {
    const offenders: string[] = [];
    for (const file of walk(root)) {
      const text = readFileSync(file, "utf8");
      const lines = text.split("\n");
      lines.forEach((line, i) => {
        for (const re of PATTERNS) {
          re.lastIndex = 0;
          for (const m of line.matchAll(re)) {
            if (parseFloat(m[1]) < MIN_PX) {
              offenders.push(`${path.relative(root, file)}:${i + 1}: ${m[0].trim()}`);
            }
          }
        }
      });
    }
    expect(offenders).toEqual([]);
  });
});

// The literal font sizes barred from the files below: 12.5px, which is off
// the scale entirely, and bare 13px/15px, which are on it but name no token
// (they equal --text-sm/--text-base). Deliberately not the whole scale --
// 17px/26px literals also sit in these files and are not barred, so a rule
// covering every on-scale value would flag those too.
const BYPASSED_LITERALS_PX = [12.5, 13, 15];

// The files held to that rule. It is a per-file rule rather than a tree-wide
// one because on-scale literals are still widespread elsewhere; the tree-wide
// invariant is the CJK type floor above (no size below 12px), which every
// file must satisfy. Adding a file here is a commitment to keep it free of
// the literals above, so add one only after converting it.
const TOKEN_BYPASS_AUDITED_FILES = [
  "pages/admin/AdminBoardPage.tsx",
  "pages/LoginPage.css",
  "components/paramPills/RoutePickerPill.css",
  "components/SettingsDrawer.tsx",
  "pages/admin/AdminUsersPage.tsx",
  "tabs/ask/investigation.css",
  "tabs/ask/stopEvidence.css",
  "tabs/map/operationsMap.css",
  "pages/admin/AdminFlagsPage.tsx",
  "components/Sidebar.tsx",
];

describe("type scale — audited files reference tokens, not scale-matching literals", () => {
  it("declares no literal 12.5/13/15px font size in any audited file", () => {
    const offenders: string[] = [];
    for (const relPath of TOKEN_BYPASS_AUDITED_FILES) {
      const file = path.join(root, relPath);
      const text = readFileSync(file, "utf8");
      const lines = text.split("\n");
      lines.forEach((line, i) => {
        for (const re of PATTERNS) {
          re.lastIndex = 0;
          for (const m of line.matchAll(re)) {
            const px = parseFloat(m[1]);
            if (BYPASSED_LITERALS_PX.includes(px)) {
              offenders.push(`${relPath}:${i + 1}: ${m[0].trim()}`);
            }
          }
        }
      });
    }
    expect(offenders).toEqual([]);
  });
});
