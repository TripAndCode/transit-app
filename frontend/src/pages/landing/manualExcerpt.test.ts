import { describe, it, expect } from "vitest";
import { extractSection, firstParagraph, manualExcerptFor } from "./manualExcerpt";

const SAMPLE_MANUAL = `# Delay Dashboard — User Manual

## Table of contents

- [1. Choosing an agency](#1-choosing-an-agency)

## 3. Overview tab — "what's happening right now"

This is the first tab shown after signing in. **It's the place to check, at a glance, how
delayed things are overall today.**

![Overview tab](./02-overview.en.png)

Key things to look at:

- **NETWORK AVG DELAY**: the average delay in minutes.

---

## 4. Map tab — "where it's happening"

The map shows average delay at each stop/station as a colored circle (bubble).

![Map tab](./03-map.en.png)

- **Color**: represents delay size.
`;

describe("extractSection", () => {
  it("extracts the body between a numbered heading and the next ## heading", () => {
    const section = extractSection(SAMPLE_MANUAL, 3);
    expect(section).toContain("This is the first tab shown after signing in");
    expect(section).not.toContain("Map tab");
  });

  it("extracts to end of document for the last section", () => {
    const section = extractSection(SAMPLE_MANUAL, 4);
    expect(section).toContain("The map shows average delay");
  });

  it("returns null when no heading with that number exists", () => {
    expect(extractSection(SAMPLE_MANUAL, 99)).toBeNull();
  });
});

describe("firstParagraph", () => {
  it("skips a leading image reference and stops at the next blank line", () => {
    const body = extractSection(SAMPLE_MANUAL, 3)!;
    const paragraph = firstParagraph(body);
    expect(paragraph).toBe(
      "This is the first tab shown after signing in. It's the place to check, at a glance, how delayed things are overall today.",
    );
  });

  it("strips markdown bold markers", () => {
    expect(firstParagraph("**bold** and plain")).toBe("bold and plain");
  });

  it("returns an empty string when a section is nothing but a leading image", () => {
    expect(firstParagraph("![only an image](./x.png)\n")).toBe("");
  });

  it("skips a leading image and returns the paragraph that follows it", () => {
    expect(firstParagraph("![only an image](./x.png)\n\nSome later paragraph.")).toBe(
      "Some later paragraph.",
    );
  });
});

describe("manualExcerptFor", () => {
  it("resolves the overview tab's excerpt from the manual's heading number 3", () => {
    expect(manualExcerptFor(SAMPLE_MANUAL, "overview")).toBe(
      "This is the first tab shown after signing in. It's the place to check, at a glance, how delayed things are overall today.",
    );
  });

  it("resolves the map tab's excerpt from heading number 4", () => {
    expect(manualExcerptFor(SAMPLE_MANUAL, "map")).toBe(
      "The map shows average delay at each stop/station as a colored circle (bubble).",
    );
  });

  it("returns null when the manual is missing the tab's section entirely", () => {
    expect(manualExcerptFor(SAMPLE_MANUAL, "ask")).toBeNull();
  });
});
