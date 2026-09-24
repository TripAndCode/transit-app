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

// The literal pixel values this fix removed from the files below: 12.5px (an
// off-scale value -- no --text-* step is 12.5) and bare 13px/15px (on-scale,
// but naming no token -- they happen to equal --text-sm/--text-base). Not the
// whole --text-* scale: this fix did not touch every on-scale literal (e.g. a
// pre-existing 17px/26px in these same files, which equal --text-md/--text-xl
// but were out of its scope), so checking the full scale here would flag
// those too.
const BYPASSED_LITERALS_PX = [12.5, 13, 15];

// Files a design-token-bypass audit converted from hardcoded scale-matching
// literals (12.5px, an off-scale value that rounded a --text-xs/13px caption
// down; bare 13px/15px, which happen to equal --text-sm/--text-base but named
// no token) to `var(--text-*)`. Scoped to these files, not the whole tree:
// dozens of other components still hardcode on-scale sizes like 13px/15px
// coincidentally, and converting every one of those is a separate, much
// larger effort than this fix, whose bug was specifically the 12.5/13/15
// literals inside these files -- see the CJK type floor test above for the
// tree-wide invariant (no size below the 12px floor) that check does cover.
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
  it("declares no literal 12.5/13/15px font size in the files this fix converted", () => {
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
