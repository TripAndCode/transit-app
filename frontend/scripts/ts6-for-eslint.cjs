// Hands typescript-eslint the TypeScript 6 compiler while the project itself
// builds and type-checks with TypeScript 7.
//
// typescript-eslint reads `ts.versionMajorMinor` at import time and throws on
// major >= 7 (peer range `>=4.8.4 <6.1.0`); it has no degraded mode. Running
// it against the TS 6 API side by side is upstream's documented answer until
// typescript-eslint supports TS >= 7.1, at which point this file, the
// `typescript-6` dependency, its two call sites and .npmrc's
// `legacy-peer-deps` all go together.
//
// npm cannot express this in the dependency tree: `typescript` is a peer
// dependency, and npm refuses to nest a second copy to satisfy a scoped
// `overrides` entry -- it fails the install with ERESOLVE, and overriding the
// range crashes the installer outright. What is left is to rewrite the request
// inside whichever process loads the linter, and there are two: the CLI, which
// takes this via `--require` on the `lint` scripts, and the vitest worker that
// runs the flat config through ESLint's Linter API, which takes it via
// `setupFiles`. A vite `resolve.alias` does not reach that second one --
// typescript-eslint is externalized CJS, loaded through Node's own require
// rather than vite's transform.
//
// The rewrite is an exact match, not a prefix, so only a bare
// `require("typescript")` is redirected and nothing else in the graph moves.
const Module = require("node:module");

const originalResolveFilename = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  return originalResolveFilename.call(this, request === "typescript" ? "typescript-6" : request, ...rest);
};
