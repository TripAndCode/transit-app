// The roots Vite may serve files from, for the dev server and the test runner
// alike. A worktree may link another checkout's node_modules (the push gate links
// the main checkout's into one that has none). A `?url` import resolves to the
// link's real path, outside this workspace, and Vite refuses to serve it unless
// that path is allowed too.

import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { searchForWorkspaceRoot } from "vite";

export function servedRoots() {
  return [
    searchForWorkspaceRoot(process.cwd()),
    realpathSync(fileURLToPath(new URL("../node_modules", import.meta.url))),
  ];
}
