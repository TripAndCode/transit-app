// Fails when the React Compiler skips a component or hook anywhere in src/.
//
// The compiler skips a whole function when it meets something it cannot
// compile: syntax it has not implemented (a try/finally, a conditional
// expression inside a try), an internal invariant, or a suppressed React
// lint rule. The skipped function then runs uncompiled, silently, with none
// of the memoization the rest of the app relies on. ESLint's react-hooks
// rules report only some of these, so this runs the compiler itself, with
// the options the build uses, over every non-test source file. Those are
// none: vite.config.ts and vitest.config.ts call reactCompilerPreset() bare,
// which tests/frontend/check_react_compiler.test.mjs pins.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const require = createRequire(import.meta.url);
const babel = require("@babel/core");
const compilerPlugin = require.resolve("babel-plugin-react-compiler");

/** A logger event that means a function was skipped, as `{ line, reason }`,
 *  or null. `CompileError` is the compiler's own diagnosis; `PipelineError`
 *  is an exception it did not anticipate, which skips the function as well. */
export function skippedFunction(event) {
  if (event.kind === "PipelineError") {
    return { line: event.fnLoc?.start?.line ?? null, reason: `compiler crashed: ${String(event.data ?? "unknown")}` };
  }
  if (event.kind !== "CompileError") return null;
  const detail = event.detail ?? {};
  const options = detail.options ?? {};
  const loc = detail.loc ?? options.loc ?? event.fnLoc;
  return { line: loc?.start?.line ?? null, reason: detail.reason ?? options.reason ?? "unknown" };
}

/** The functions the compiler skips in one file's source, as `{ line, reason }`. */
export function compilerErrors(code, filename) {
  const errors = [];
  const logger = {
    logEvent(_file, event) {
      const skipped = skippedFunction(event);
      if (skipped) errors.push(skipped);
    },
  };
  babel.transformSync(code, {
    filename,
    babelrc: false,
    configFile: false,
    parserOpts: { plugins: ["typescript", "jsx"] },
    plugins: [[compilerPlugin, { logger }]],
  });
  return errors;
}

/** Every tracked non-test .ts/.tsx file under src/, filtered here rather than
 *  by a git pathspec: `src/**` in a pathspec skips the files directly in src/,
 *  the app's entry and root component among them. */
export function sourceFiles(root) {
  const listed = execFileSync("git", ["ls-files", "--", "src"], { cwd: root, encoding: "utf8" });
  return listed.split("\n").filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$|\.d\.ts$/.test(f));
}

function main() {
  const root = resolve(fileURLToPath(import.meta.url), "../..");
  let failures = 0;
  for (const file of sourceFiles(root)) {
    const path = resolve(root, file);
    for (const { line, reason } of compilerErrors(readFileSync(path, "utf8"), path)) {
      failures += 1;
      console.error(`${relative(root, path)}:${line ?? "?"}: ${reason}`);
    }
  }
  if (failures > 0) {
    console.error(`check-react-compiler: ${failures} function(s) the React Compiler skips.`);
    process.exit(1);
  }
  console.log("check-react-compiler: OK — the compiler compiles every component and hook.");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
