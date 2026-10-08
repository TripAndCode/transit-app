import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { compilerErrors, skippedFunction, sourceFiles } from "../../frontend/scripts/check-react-compiler.mjs";

const FRONTEND = fileURLToPath(new URL("../../frontend", import.meta.url));

const reasons = (code) => compilerErrors(code, "src/Fixture.tsx").map((e) => e.reason);

test("a clean component compiles", () => {
  const code = 'import { useState } from "react";\nexport function Counter() {\n  const [n, setN] = useState(0);\n  return <button onClick={() => setN(n + 1)}>{n}</button>;\n}\n';
  assert.deepEqual(reasons(code), []);
});

test("a try/finally in a component is reported", () => {
  const code =
    'import { useState } from "react";\nexport function Saver({ save }) {\n  const [busy, setBusy] = useState(false);\n' +
    "  async function onClick() {\n    setBusy(true);\n    try { await save(); } finally { setBusy(false); }\n  }\n" +
    "  return <button disabled={busy} onClick={onClick} />;\n}\n";
  assert.equal(reasons(code).length, 1);
  assert.match(reasons(code)[0], /TryStatement/);
});

test("a conditional expression inside a component's try is reported", () => {
  const code =
    "export function Exporter({ name, save }) {\n  async function onClick() {\n" +
    '    try { await save(`${name ?? "export"}.png`); } catch { /* ignore */ }\n  }\n' +
    "  return <button onClick={onClick} />;\n}\n";
  assert.match(reasons(code)[0] ?? "", /value blocks/);
});

test("a suppressed React lint rule is reported", () => {
  const code =
    'import { useEffect } from "react";\nexport function useTicker(key, onTick) {\n' +
    "  useEffect(() => {\n    onTick(key);\n    // eslint-disable-next-line react-hooks/exhaustive-deps\n  }, [key]);\n}\n";
  assert.match(reasons(code)[0] ?? "", /ESLint rules were disabled/);
});

test("a try/finally outside any component or hook is not reported", () => {
  const code = "export async function saveAll(save, done) {\n  try { await save(); } finally { done(); }\n}\n";
  assert.deepEqual(reasons(code), []);
});

test("reports where the skipped code is", () => {
  const code =
    'import { useState } from "react";\nexport function Saver({ save }) {\n  const [busy, setBusy] = useState(false);\n' +
    "  async function onClick() {\n    try { await save(); } finally { setBusy(false); }\n  }\n" +
    "  return <button disabled={busy} onClick={onClick} />;\n}\n";
  assert.equal(compilerErrors(code, "src/Fixture.tsx")[0].line, 5);
});

test("scans the files directly under src as well as nested ones, and no tests", () => {
  const files = sourceFiles(FRONTEND);
  assert.ok(files.includes("src/App.tsx"));
  assert.ok(files.includes("src/main.tsx"));
  assert.ok(files.includes("src/components/ExportMenu.tsx"));
  assert.ok(!files.some((f) => /\.test\.tsx?$|\.d\.ts$/.test(f)));
});

test("counts a compiler crash as a skipped function, and a success or a skip of a non-component as none", () => {
  const crash = skippedFunction({ kind: "PipelineError", fnLoc: { start: { line: 9 } }, data: "TypeError: boom" });
  assert.deepEqual(crash, { line: 9, reason: "compiler crashed: TypeError: boom" });
  assert.equal(skippedFunction({ kind: "CompileSuccess" }), null);
  assert.equal(skippedFunction({ kind: "CompileSkip" }), null);
});
