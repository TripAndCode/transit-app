// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

const globalCss = readFileSync(resolve(__dirname, "./global.css"), "utf-8");
const focusedAnalysisCss = readFileSync(resolve(__dirname, "./focusedAnalysis.css"), "utf-8");

describe("shared print stylesheet", () => {
  it("declares exactly one @media print block, in global.css (loaded on every page)", () => {
    expect(globalCss.match(/@media print/g)).toHaveLength(1);
    expect(focusedAnalysisCss.match(/@media print/g)).toBeNull();
  });

  it("hides the export menu itself when printing", () => {
    const start = globalCss.indexOf("@media print");
    let depth = 0;
    let end = start;
    for (let i = start; i < globalCss.length; i++) {
      if (globalCss[i] === "{") depth++;
      if (globalCss[i] === "}") {
        depth--;
        if (depth === 0) {
          end = i + 1;
          break;
        }
      }
    }
    expect(globalCss.slice(start, end)).toContain(".export-menu");
  });

  it("hides the print frame on screen and shows it only inside the print block", () => {
    const start = globalCss.indexOf("@media print");
    let depth = 0;
    let end = start;
    for (let i = start; i < globalCss.length; i++) {
      if (globalCss[i] === "{") depth++;
      if (globalCss[i] === "}") {
        depth--;
        if (depth === 0) {
          end = i + 1;
          break;
        }
      }
    }
    const printBlock = globalCss.slice(start, end);
    const screenCss = globalCss.slice(0, start) + globalCss.slice(end);
    expect(screenCss).toMatch(/\.print-frame\s*\{[^}]*display:\s*none/);
    expect(printBlock).toMatch(/\.print-frame[^{]*\{[^}]*display:\s*block/);
  });

  // A stamp footnote writes its links out (the methodology document's URL),
  // and a URL is one unbroken word: without this it runs past a narrow
  // screen's edge and off the printed page.
  it("lets a report footnote wrap a long link anywhere, on screen and on paper", () => {
    expect(globalCss).toMatch(/(^|\n)\.report-footnote\s*\{[^}]*overflow-wrap:\s*anywhere/);
  });
});
