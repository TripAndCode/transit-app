import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";

import { localeChunkMap } from "../../frontend/scripts/localeChunkMap.mjs";

const PLACEHOLDER = "/*__LOCALE_CHUNKS__*/ {}";
const HTML = `<head><script>(function (chunks) {})(${PLACEHOLDER});</script></head>`;

const tmpDirs = [];
after(() => {
  for (const dir of tmpDirs) rmSync(dir, { recursive: true, force: true });
});

function fixtureRoot(locales = ["en", "ja"]) {
  const root = mkdtempSync(join(tmpdir(), "locale-chunk-map-"));
  tmpDirs.push(root);
  mkdirSync(join(root, "src/i18n/locales"), { recursive: true });
  for (const locale of locales) writeFileSync(join(root, "src/i18n/locales", `${locale}.json`), "{}");
  return root;
}

function localeChunk(root, locale, fileName) {
  return { type: "chunk", fileName, facadeModuleId: join(root, "src/i18n/locales", `${locale}.json`) };
}

function transform(root, html, bundle, base = "/") {
  const plugin = localeChunkMap();
  plugin.configResolved({ root, base });
  return plugin.transformIndexHtml.handler(html, { bundle });
}

test("fills the placeholder with each locale's own chunk URL", () => {
  const root = fixtureRoot();
  const bundle = {
    "assets/index.js": { type: "chunk", fileName: "assets/index.js", facadeModuleId: join(root, "index.html") },
    "assets/en-a1.js": localeChunk(root, "en", "assets/en-a1.js"),
    "assets/ja-b2.js": localeChunk(root, "ja", "assets/ja-b2.js"),
    "assets/index.css": { type: "asset", fileName: "assets/index.css" },
  };
  const html = transform(root, HTML, bundle);
  assert.equal(
    html,
    `<head><script>(function (chunks) {})({"en":"/assets/en-a1.js","ja":"/assets/ja-b2.js"});</script></head>`,
  );
});

test("prefixes chunk URLs with the configured base", () => {
  const root = fixtureRoot(["ja"]);
  const html = transform(root, HTML, { "assets/ja-b2.js": localeChunk(root, "ja", "assets/ja-b2.js") }, "/app/");
  assert.match(html, /\{"ja":"\/app\/assets\/ja-b2\.js"\}/);
});

test("a locale folded into another chunk fails the build", () => {
  const root = fixtureRoot();
  // A static import leaves the locale in a shared chunk with no facade module.
  const bundle = {
    "assets/ja-b2.js": localeChunk(root, "ja", "assets/ja-b2.js"),
    "assets/en-c3.js": { type: "chunk", fileName: "assets/en-c3.js", facadeModuleId: null },
  };
  assert.throws(() => transform(root, HTML, bundle), /no chunk of its own for en\b/);
});

test("index.html without the placeholder fails the build", () => {
  const root = fixtureRoot();
  const bundle = {
    "assets/en-a1.js": localeChunk(root, "en", "assets/en-a1.js"),
    "assets/ja-b2.js": localeChunk(root, "ja", "assets/ja-b2.js"),
  };
  assert.throws(() => transform(root, "<head></head>", bundle), /exactly once/);
  assert.throws(() => transform(root, HTML + HTML, bundle), /exactly once/);
});
