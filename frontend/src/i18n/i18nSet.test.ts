// @vitest-environment node
// jsdom's global URL constructor doesn't resolve a `file:` base the way
// node's does (see src/styles/tableWrap.test.ts); this suite reads real
// files by path, so it runs under the plain node environment.
import { describe, expect, test } from "vitest";
import { mkdtempSync, writeFileSync, readFileSync, copyFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { I18nSetError, setKeyInText, deleteKeyInText, run } from "../../scripts/i18n-set.mjs";

// This suite never reads or writes the real locale files directly. Most
// cases use a small synthetic fixture; one dedicated case copies the real
// files into a temp directory to exercise their actual mixed NBSP escaping
// (see i18n-set.mjs's module comment) — a synthetic fixture alone would let
// a naive parse-and-restringify implementation pass by accident.

const FIXTURE_EN = `{
  "common": {
    "hello": "Hello",
    "bye": "Bye"
  },
  "simple": "Simple value"
}
`;

const FIXTURE_JA = `{
  "common": {
    "hello": "こんにちは",
    "bye": "さようなら"
  },
  "simple": "シンプルな値"
}
`;

function makeLocaleDir(en = FIXTURE_EN, ja = FIXTURE_JA): string {
  const dir = mkdtempSync(join(tmpdir(), "i18n-set-test-"));
  writeFileSync(join(dir, "en.json"), en, "utf8");
  writeFileSync(join(dir, "ja.json"), ja, "utf8");
  return dir;
}

describe("setKeyInText", () => {
  test("adds a new key into an existing object", () => {
    const out = setKeyInText(FIXTURE_EN, ["common", "welcome"], "Welcome");
    expect(out).toBe(
      `{
  "common": {
    "hello": "Hello",
    "bye": "Bye",
    "welcome": "Welcome"
  },
  "simple": "Simple value"
}
`,
    );
  });

  test("updates an existing leaf", () => {
    const out = setKeyInText(FIXTURE_EN, ["common", "hello"], "Hi");
    expect(out).toContain('"hello": "Hi"');
    expect(out).not.toContain('"hello": "Hello"');
  });

  test("creates a brand new nested chain", () => {
    const out = setKeyInText(FIXTURE_EN, ["feature", "panel", "title"], "Title");
    expect(out).toBe(
      `{
  "common": {
    "hello": "Hello",
    "bye": "Bye"
  },
  "simple": "Simple value",
  "feature": {
    "panel": {
      "title": "Title"
    }
  }
}
`,
    );
  });

  test("re-setting a key to its current value is a byte-for-byte no-op", () => {
    const out = setKeyInText(FIXTURE_EN, ["common", "hello"], "Hello");
    expect(out).toBe(FIXTURE_EN);
  });

  test("refuses to overwrite an object key with a string", () => {
    expect(() => setKeyInText(FIXTURE_EN, ["common"], "oops")).toThrow(I18nSetError);
  });

  test("refuses to turn a string leaf into an object", () => {
    expect(() => setKeyInText(FIXTURE_EN, ["simple", "nested"], "oops")).toThrow(I18nSetError);
    expect(() => setKeyInText(FIXTURE_EN, ["common", "hello", "nested"], "oops")).toThrow(I18nSetError);
  });
});

describe("deleteKeyInText", () => {
  test("deletes a leaf, keeping siblings", () => {
    const out = deleteKeyInText(FIXTURE_EN, ["common", "hello"]);
    expect(out).toBe(
      `{
  "common": {
    "bye": "Bye"
  },
  "simple": "Simple value"
}
`,
    );
  });

  test("prunes a parent object left empty by the deletion, but never the root", () => {
    const withOneChild = `{
  "common": {
    "hello": "Hello"
  }
}
`;
    const out = deleteKeyInText(withOneChild, ["common", "hello"]);
    expect(out).toBe("{}\n");
  });

  test("is a no-op for a key that does not exist", () => {
    const out = deleteKeyInText(FIXTURE_EN, ["does", "not", "exist"]);
    expect(out).toBe(FIXTURE_EN);
  });

  test("refuses to delete an object directly", () => {
    expect(() => deleteKeyInText(FIXTURE_EN, ["common"])).toThrow(I18nSetError);
  });
});

describe("run (CLI) against a locales dir", () => {
  test("sets a key in both locales", () => {
    const dir = makeLocaleDir();
    const summary = run(["--locales-dir", dir, "common.welcome", "Welcome", "ようこそ"]);
    expect(summary).toEqual(["set common.welcome"]);
    expect(readFileSync(join(dir, "en.json"), "utf8")).toContain('"welcome": "Welcome"');
    expect(readFileSync(join(dir, "ja.json"), "utf8")).toContain('"welcome": "ようこそ"');
  });

  test("applies a batch file in one command", () => {
    const dir = makeLocaleDir();
    const batchPath = join(dir, "batch.json");
    writeFileSync(
      batchPath,
      JSON.stringify({
        "common.welcome": { en: "Welcome", ja: "ようこそ" },
        "feature.title": { en: "Feature", ja: "機能" },
      }),
      "utf8",
    );
    const summary = run(["--locales-dir", dir, "--file", batchPath]);
    expect(summary).toEqual(["set common.welcome", "set feature.title"]);
    expect(readFileSync(join(dir, "en.json"), "utf8")).toContain('"welcome": "Welcome"');
    expect(readFileSync(join(dir, "en.json"), "utf8")).toContain('"feature": {\n    "title": "Feature"');
  });

  test("deletes a key through the CLI", () => {
    const dir = makeLocaleDir();
    const summary = run(["--locales-dir", dir, "--delete", "common.hello"]);
    expect(summary).toEqual(["deleted common.hello"]);
    expect(readFileSync(join(dir, "en.json"), "utf8")).not.toContain("hello");
    expect(readFileSync(join(dir, "ja.json"), "utf8")).not.toContain("hello");
  });

  test("writes neither file when a batch entry conflicts partway through", () => {
    const dir = makeLocaleDir();
    const batchPath = join(dir, "batch.json");
    writeFileSync(
      batchPath,
      JSON.stringify({
        "feature.title": { en: "Feature", ja: "機能" },
        // "common" already holds an object; this entry must fail and
        // leave both locale files exactly as they were, including the
        // first entry above.
        common: { en: "oops", ja: "oops" },
      }),
      "utf8",
    );
    expect(() => run(["--locales-dir", dir, "--file", batchPath])).toThrow(I18nSetError);
    expect(readFileSync(join(dir, "en.json"), "utf8")).toBe(FIXTURE_EN);
    expect(readFileSync(join(dir, "ja.json"), "utf8")).toBe(FIXTURE_JA);
  });

  test("errors on --delete for a key missing from both locales", () => {
    const dir = makeLocaleDir();
    expect(() => run(["--locales-dir", dir, "--delete", "nope.not.here"])).toThrow(I18nSetError);
  });

  test("rejects the wrong number of positional arguments", () => {
    const dir = makeLocaleDir();
    expect(() => run(["--locales-dir", dir, "only.one.arg"])).toThrow(I18nSetError);
  });
});

describe("run against real locale files (temp copies)", () => {
  test("re-setting a key with mixed NBSP escaping round-trips byte for byte", () => {
    const realDir = fileURLToPath(new URL("./locales/", import.meta.url));
    const dir = mkdtempSync(join(tmpdir(), "i18n-set-real-"));
    copyFileSync(join(realDir, "en.json"), join(dir, "en.json"));
    copyFileSync(join(realDir, "ja.json"), join(dir, "ja.json"));
    const originalEn = readFileSync(join(dir, "en.json"), "utf8");
    const originalJa = readFileSync(join(dir, "ja.json"), "utf8");

    const en = JSON.parse(originalEn);
    const ja = JSON.parse(originalJa);

    // scope.duration_min: en.json escapes its non-breaking space with a
    // backslash-u sequence; this key alone would catch a `JSON.parse` +
    // `JSON.stringify` round trip, which always re-emits it as a literal
    // character instead.
    run(["--locales-dir", dir, "scope.duration_min", en.scope.duration_min, ja.scope.duration_min]);
    // landing.hero_map.kpi.delayed: en.json holds a literal NBSP character
    // here instead of an escape — the opposite case.
    run([
      "--locales-dir",
      dir,
      "landing.hero_map.kpi.delayed",
      en.landing.hero_map.kpi.delayed,
      ja.landing.hero_map.kpi.delayed,
    ]);

    expect(readFileSync(join(dir, "en.json"), "utf8")).toBe(originalEn);
    expect(readFileSync(join(dir, "ja.json"), "utf8")).toBe(originalJa);
  });
});

test("locales directory exists at the expected relative path", () => {
  const realDir = fileURLToPath(new URL("./locales/", import.meta.url));
  expect(existsSync(join(realDir, "en.json"))).toBe(true);
});
