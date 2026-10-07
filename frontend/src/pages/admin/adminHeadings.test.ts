import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

const adminDir = resolve(process.cwd(), "src/pages/admin");
const pages = readdirSync(adminDir).filter((f) => /^Admin\w+Page\.tsx$/.test(f) && f !== "AdminUserDetailPage.tsx");

describe("admin page headings", () => {
  it.each(pages)("%s heads itself with the shared PageHeader, not a hand-sized h1", (file) => {
    const source = readFileSync(resolve(adminDir, file), "utf8");
    expect(source).toContain("<PageHeader");
    expect(source).not.toMatch(/<h1[\s>]/);
  });
});
