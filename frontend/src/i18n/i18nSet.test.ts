// @vitest-environment node
// jsdom's global URL constructor doesn't resolve a `file:` base the way
// node's does (see src/styles/tableWrap.test.ts); this suite reads real
// files by path, so it runs under the plain node environment.
import { describe, expect, test } from "vitest";
import { mkdtempSync, writeFileSync, readFileSync, copyFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { I18nSetError, serialize, parseArgv, USAGE, run } from "../../scripts/i18n-set.mjs";

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

describe("serialize", () => {
  test("both real locale files already equal serialize(parse(raw))", () => {
    const realDir = fileURLToPath(new URL("./locales/", import.meta.url));
    for (const name of ["en.json", "ja.json"]) {
      const raw = readFileSync(join(realDir, name), "utf8");
      expect(serialize(JSON.parse(raw))).toBe(raw);
    }
  });

  test("escapes a non-breaking space the same way JSON.stringify writes it literally", () => {
    const withLiteralNbsp = { label: "5+ min late" };
    expect(serialize(withLiteralNbsp)).toBe('{\n  "label": "5+\\u00a0min late"\n}\n');
  });
});

describe("parseArgv", () => {
  test("throws before parsing a dangling flag value", () => {
    expect(() => parseArgv(["--locales-dir"])).toThrow(I18nSetError);
    expect(() => parseArgv(["--locales-dir"])).toThrow(/--locales-dir/);
  });

  test("throws when a flag's value looks like another flag", () => {
    expect(() => parseArgv(["--locales-dir", "--file", "x"])).toThrow(/--locales-dir/);
    expect(() => parseArgv(["--file", "--delete"])).toThrow(/--file/);
  });

  test("parses all three flags plus positionals", () => {
    const parsed = parseArgv(["--locales-dir", "/tmp/x", "a.b", "en text", "ja text"]);
    expect(parsed).toEqual({
      positionals: ["a.b", "en text", "ja text"],
      file: null,
      deleteKey: null,
      localesDir: "/tmp/x",
    });
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

  test("updates an existing leaf", () => {
    const dir = makeLocaleDir();
    run(["--locales-dir", dir, "common.hello", "Hi", "やあ"]);
    const en = readFileSync(join(dir, "en.json"), "utf8");
    expect(en).toContain('"hello": "Hi"');
    expect(en).not.toContain('"hello": "Hello"');
  });

  test("creates a brand new nested chain", () => {
    const dir = makeLocaleDir();
    run(["--locales-dir", dir, "feature.panel.title", "Title", "タイトル"]);
    expect(readFileSync(join(dir, "en.json"), "utf8")).toContain('"feature": {\n    "panel": {\n      "title": "Title"');
  });

  test("re-setting a key to its current value leaves the files byte-for-byte unchanged", () => {
    const dir = makeLocaleDir();
    run(["--locales-dir", dir, "common.hello", "Hello", "こんにちは"]);
    expect(readFileSync(join(dir, "en.json"), "utf8")).toBe(FIXTURE_EN);
    expect(readFileSync(join(dir, "ja.json"), "utf8")).toBe(FIXTURE_JA);
  });

  test("refuses to overwrite an object key with a string", () => {
    const dir = makeLocaleDir();
    expect(() => run(["--locales-dir", dir, "common", "oops", "oops"])).toThrow(I18nSetError);
  });

  test("refuses to turn a string leaf into an object", () => {
    const dir = makeLocaleDir();
    expect(() => run(["--locales-dir", dir, "simple.nested", "oops", "oops"])).toThrow(I18nSetError);
    expect(() => run(["--locales-dir", dir, "common.hello.nested", "oops", "oops"])).toThrow(I18nSetError);
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

  test("writes neither file when a batch entry conflicts partway through", () => {
    const dir = makeLocaleDir();
    const batchPath = join(dir, "batch.json");
    writeFileSync(
      batchPath,
      JSON.stringify({
        "feature.title": { en: "Feature", ja: "機能" },
        // "common" already holds an object; this entry must fail and leave
        // both locale files exactly as they were, including the first
        // entry above.
        common: { en: "oops", ja: "oops" },
      }),
      "utf8",
    );
    expect(() => run(["--locales-dir", dir, "--file", batchPath])).toThrow(I18nSetError);
    expect(readFileSync(join(dir, "en.json"), "utf8")).toBe(FIXTURE_EN);
    expect(readFileSync(join(dir, "ja.json"), "utf8")).toBe(FIXTURE_JA);
  });

  test.each([
    ["not json at all", "{not valid json"],
    ["a null root", "null"],
    ["an array root", '["a", "b"]'],
    ["a scalar root", '"just a string"'],
    ["an entry missing ja", '{"a.b": {"en": "hi"}}'],
    ["an entry missing en", '{"a.b": {"ja": "hi"}}'],
    ["an entry that is not an object", '{"a.b": "hi"}'],
  ])("--file rejects %s", (_label, contents) => {
    const dir = makeLocaleDir();
    const batchPath = join(dir, "batch.json");
    writeFileSync(batchPath, contents, "utf8");
    expect(() => run(["--locales-dir", dir, "--file", batchPath])).toThrow(I18nSetError);
    expect(readFileSync(join(dir, "en.json"), "utf8")).toBe(FIXTURE_EN);
    expect(readFileSync(join(dir, "ja.json"), "utf8")).toBe(FIXTURE_JA);
  });

  test("deletes a key through the CLI, keeping siblings", () => {
    const dir = makeLocaleDir();
    const summary = run(["--locales-dir", dir, "--delete", "common.hello"]);
    expect(summary).toEqual(["deleted common.hello"]);
    expect(readFileSync(join(dir, "en.json"), "utf8")).not.toContain("hello");
    expect(readFileSync(join(dir, "ja.json"), "utf8")).not.toContain("hello");
    expect(readFileSync(join(dir, "en.json"), "utf8")).toContain("bye");
  });

  test("deleting a key prunes a parent object left empty by the deletion, but never the root", () => {
    const dir = makeLocaleDir(
      `{
  "common": {
    "hello": "Hello"
  }
}
`,
      `{
  "common": {
    "hello": "こんにちは"
  }
}
`,
    );
    run(["--locales-dir", dir, "--delete", "common.hello"]);
    expect(readFileSync(join(dir, "en.json"), "utf8")).toBe("{}\n");
    expect(readFileSync(join(dir, "ja.json"), "utf8")).toBe("{}\n");
  });

  test("refuses to delete an object directly", () => {
    const dir = makeLocaleDir();
    expect(() => run(["--locales-dir", dir, "--delete", "common"])).toThrow(I18nSetError);
  });

  test("errors on --delete for a key missing from both locales", () => {
    const dir = makeLocaleDir();
    expect(() => run(["--locales-dir", dir, "--delete", "nope.not.here"])).toThrow(I18nSetError);
  });

  test("rejects the wrong number of positional arguments", () => {
    const dir = makeLocaleDir();
    expect(() => run(["--locales-dir", dir, "only.one.arg"])).toThrow(I18nSetError);
  });

  test("a dangling --locales-dir throws before resolving any directory, real or not", () => {
    // A missing flag value must never fall through to the default (real)
    // locales directory: `localesDir` would be `undefined`, which is as
    // falsy as the "no flag given" default `null`.
    expect(() => run(["--locales-dir"])).toThrow(I18nSetError);
    expect(() => run(["--locales-dir"])).toThrow(/--locales-dir/);
  });

  test("usage errors include the full USAGE text", () => {
    const dir = makeLocaleDir();
    expect.assertions(1);
    try {
      run(["--locales-dir", dir, "only.one.arg"]);
    } catch (err) {
      expect((err as Error).message).toContain(USAGE);
    }
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

    run(["--locales-dir", dir, "scope.duration_min", en.scope.duration_min, ja.scope.duration_min]);
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
