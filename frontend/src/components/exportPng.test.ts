import { describe, it, expect, afterEach } from "vitest";
import { serializeSvgForImage } from "./exportPng";

const SVG_NS = "http://www.w3.org/2000/svg";

// --heat-ring stands for a per-instance property that no element here sets.
function chart(): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg") as SVGSVGElement;
  const line = document.createElementNS(SVG_NS, "path");
  line.setAttribute("stroke", "var(--accent)");
  line.setAttribute("style", "fill: var(--heat-ring, #abcdef); opacity: 0.5");
  const label = document.createElementNS(SVG_NS, "text");
  label.setAttribute("fill", "var(--heat-ring, var(--accent))");
  label.setAttribute("stroke", "var(--heat-ring, rgb(1, 2, 3))");
  svg.append(line, label);
  document.body.appendChild(svg);
  return svg;
}

describe("serializeSvgForImage", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    document.documentElement.style.removeProperty("--accent");
  });

  it("replaces every var() with the value the live element sees, or its fallback", () => {
    document.documentElement.style.setProperty("--accent", "#123456");
    const markup = serializeSvgForImage(chart());

    expect(markup).not.toContain("var(");
    expect(markup).toContain('stroke="#123456"');
    expect(markup).toContain("fill: #abcdef; opacity: 0.5");
    expect(markup).toContain('fill="#123456"');
    expect(markup).toContain('stroke="rgb(1, 2, 3)"');
  });

  it("leaves the page's own SVG untouched", () => {
    document.documentElement.style.setProperty("--accent", "#123456");
    const svg = chart();
    serializeSvgForImage(svg);
    expect(svg.querySelector("path")?.getAttribute("stroke")).toBe("var(--accent)");
  });
});
