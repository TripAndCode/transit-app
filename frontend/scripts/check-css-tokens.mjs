#!/usr/bin/env node
// Two static checks over the whole source tree, so a stray CSS custom
// property or a hand-typed z-index number doesn't reintroduce the drift
// this repo just cleaned up (undefined --x tokens silently rendering with
// no value; z-index literals picked ad hoc instead of from the shared
// ladder in src/styles/zIndex.ts).
//
// 1. Every `var(--x)` reference (CSS, and inline style objects in TS/TSX)
//    must resolve to a `--x: ...` declaration in styles/global.css. A
//    reference with a fallback that is itself another token
//    (`var(--x, var(--y))`) is exempt regardless, since it composes two
//    named tokens. A reference with a *literal* fallback (`var(--x,
//    <value>)`) is exempt only when `--x` is listed in
//    DYNAMIC_PER_INSTANCE_PROPERTIES (genuinely set per-instance at
//    runtime, e.g. inline `style={{ "--ops-queue-width": ... }}`) — anything
//    else with an unresolved name and a literal fallback is presumed to be a
//    design token whose name was typo'd or renamed and must still resolve.
// 2. Every `z-index:` declaration in a CSS file must be `var(--z-*)` — a
//    bare number would bypass the shared stacking-order ladder entirely
//    (see src/styles/zIndex.ts and its `--z-*` mirror in global.css).
//
// SRC_DIR can be overridden with --src-dir <path> (used by
// tests/frontend/check_css_tokens.test.mjs to run against a fixture tree
// without touching the real src/). Global.css is always read from
// <src-dir>/styles/global.css, matching this repo's actual layout.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname, extname } from "node:path";
import { fileURLToPath } from "node:url";
import { DYNAMIC_PER_INSTANCE_PROPERTIES } from "./dynamicCssProperties.mjs";

function srcDirFromArgv() {
  const flagIndex = process.argv.indexOf("--src-dir");
  if (flagIndex === -1) return null;
  const value = process.argv[flagIndex + 1];
  if (!value) {
    console.error("check-css-tokens: --src-dir requires a path argument.");
    process.exit(1);
  }
  return value;
}

const FRONTEND_DIR = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC_DIR = srcDirFromArgv() ?? join(FRONTEND_DIR, "src");
const GLOBAL_CSS_PATH = join(SRC_DIR, "styles", "global.css");

const SCAN_EXTENSIONS = [".css", ".tsx", ".ts"];

function walk(dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(full, out);
    } else if (SCAN_EXTENSIONS.includes(extname(entry.name))) {
      out.push(full);
    }
  }
  return out;
}


// A hand-rolled scan (not a single regex) because a fallback value can
// itself contain parens (e.g. `var(--accent-soft, rgba(91, 108, 173,
// 0.14))`) — a regex anchored on the first `)` would truncate that
// fallback and misreport it as the whole match.
// Comments are removed before scanning. Prose explaining a token routinely
// writes `var(--x)` to name it, and a comment naming a property that is set
// per-instance at runtime would otherwise be reported as an unresolvable
// reference -- a failure with nothing to fix, since adding a fallback to a
// sentence is meaningless. Strings are left alone: a `var()` inside one is a
// real reference the browser will resolve.
function stripComments(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

function findVarRefs(text) {
  const refs = [];
  let i = 0;
  while ((i = text.indexOf("var(--", i)) !== -1) {
    const nameAndFallbackStart = i + 4; // just past "var("
    let depth = 1;
    let j = nameAndFallbackStart;
    while (depth > 0 && j < text.length) {
      if (text[j] === "(") depth++;
      else if (text[j] === ")") depth--;
      j++;
    }
    const inner = text.slice(nameAndFallbackStart, j - 1);
    const commaIndex = inner.indexOf(",");
    const name = (commaIndex === -1 ? inner : inner.slice(0, commaIndex)).trim();
    const fallback = commaIndex === -1 ? null : inner.slice(commaIndex + 1).trim();
    // Skip anything that isn't a syntactically valid custom-property name
    // (e.g. a doc comment reading "var(--*)" to mean "any --x property") --
    // it was never a real reference to resolve in the first place.
    if (/^--[a-zA-Z0-9-]+$/.test(name)) {
      refs.push({ name, hasFallback: commaIndex !== -1, fallback, index: i });
    }
    i = j;
  }
  return refs;
}

function definedCustomProperties(globalCssText) {
  const defined = new Set();
  for (const match of globalCssText.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)) {
    defined.add(match[1]);
  }
  return defined;
}

// Matches the property's value up to the terminating `;` (or end of
// declaration block). Deliberately simple — this repo's CSS never nests a
// `;`-bearing construct (e.g. a data: URI) inside a z-index value.
const Z_INDEX_DECLARATION_RE = /z-index\s*:\s*([^;]+);/g;
const VALID_Z_INDEX_VALUE_RE = /^var\(--z-[a-zA-Z-]+\)$/;

let failed = false;

let globalCssText;
try {
  globalCssText = readFileSync(GLOBAL_CSS_PATH, "utf8");
} catch (err) {
  console.error(`check-css-tokens: could not read ${GLOBAL_CSS_PATH} (${err.message}).`);
  process.exit(1);
}
const definedVars = definedCustomProperties(globalCssText);

const files = walk(SRC_DIR);
for (const file of files) {
  const text = readFileSync(file, "utf8");
  const relPath = file.slice(SRC_DIR.length + 1);

  for (const { name, hasFallback, fallback } of findVarRefs(stripComments(text))) {
    if (definedVars.has(name)) continue;
    if (!hasFallback) {
      console.error(
        `check-css-tokens: FAIL — "${relPath}" references var(${name}) with no fallback, but ${name} is not ` +
          "defined anywhere in styles/global.css. Define it there, or give the reference a fallback " +
          `(var(${name}, <value>)) if it's meant to be set dynamically per-instance.`,
      );
      failed = true;
      continue;
    }
    // A fallback that is itself another var() reference composes two named
    // tokens and needs no definition of its own (e.g. `var(--map-badge-bg,
    // var(--bg-surface))`). A fallback that is a literal (colour, length,
    // ...) only excuses an undefined name for a known dynamic, per-instance
    // property — anything else is presumed to be a design token whose name
    // was typo'd or renamed (this is exactly the bug `var(--radius-md,
    // 10px)` was: no `--radius-md` was ever declared, and 10px is really
    // `--radius-lg` by coincidence).
    if (/^var\(/.test(fallback)) continue;
    if (DYNAMIC_PER_INSTANCE_PROPERTIES.has(name)) continue;
    console.error(
      `check-css-tokens: FAIL — "${relPath}" references var(${name}, ${fallback}), but ${name} is not defined ` +
        "anywhere in styles/global.css and its fallback is a literal value, not another token. Define " +
        `${name} in global.css, or add it to DYNAMIC_PER_INSTANCE_PROPERTIES in scripts/dynamicCssProperties.mjs if it is ` +
        "genuinely set per-instance at runtime rather than being a design token.",
    );
    failed = true;
  }

  if (extname(file) === ".css") {
    for (const match of stripComments(text).matchAll(Z_INDEX_DECLARATION_RE)) {
      const value = match[1].trim();
      if (!VALID_Z_INDEX_VALUE_RE.test(value)) {
        console.error(
          `check-css-tokens: FAIL — "${relPath}" sets "z-index: ${value}", not one of the shared ` +
            "var(--z-*) rungs (see src/styles/zIndex.ts and its mirror in styles/global.css). " +
            "Use the ladder token that matches this overlay's intended stacking order instead of a literal.",
        );
        failed = true;
      }
    }
  }
}

if (failed) {
  process.exit(1);
}
console.log(
  `check-css-tokens: OK — every var(--x) reference across ${files.length} file(s) resolves (or has a ` +
    "fallback), and every z-index declaration uses the shared ladder.",
);
