/** The index of the first comma outside any parentheses in `text`, or -1. */
function topLevelComma(text: string): number {
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === "(") depth++;
    else if (text[i] === ")") depth--;
    else if (text[i] === "," && depth === 0) return i;
  }
  return -1;
}

/** `value` with every `var(--name, fallback)` replaced by `lookup(name)`, or by
 *  its fallback (itself resolved) when the name has no value. */
function resolveCssVars(value: string, lookup: (name: string) => string): string {
  const start = value.indexOf("var(");
  if (start < 0) return value;
  let depth = 0;
  let end = start + 3;
  for (; end < value.length; end++) {
    if (value[end] === "(") depth++;
    else if (value[end] === ")" && --depth === 0) break;
  }
  if (depth !== 0) return value;
  const inner = value.slice(start + 4, end);
  const comma = topLevelComma(inner);
  const name = (comma < 0 ? inner : inner.slice(0, comma)).trim();
  const fallback = comma < 0 ? "" : inner.slice(comma + 1).trim();
  const resolved = lookup(name).trim() || resolveCssVars(fallback, lookup);
  return value.slice(0, start) + resolved + resolveCssVars(value.slice(end + 1), lookup);
}

/** The SVG as markup a standalone image can draw. An SVG loaded through
 *  `<img>` is a document of its own, without the page's custom properties,
 *  so a `var(--x)` left in it is invalid there and its colour falls back to
 *  none or black. Every one in an attribute or inline style is replaced by the
 *  value `--x` has on the live element. */
export function serializeSvgForImage(svg: SVGSVGElement): string {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  const live = [svg, ...svg.querySelectorAll("*")];
  const copies = [clone, ...clone.querySelectorAll("*")];
  live.forEach((element, index) => {
    const copy = copies[index];
    let computed: CSSStyleDeclaration | undefined;
    for (const attribute of Array.from(copy.attributes)) {
      if (!attribute.value.includes("var(")) continue;
      computed ??= getComputedStyle(element);
      const style = computed;
      copy.setAttribute(attribute.name, resolveCssVars(attribute.value, (name) => style.getPropertyValue(name)));
    }
  });
  return new XMLSerializer().serializeToString(clone);
}

/** Renders an SVG to a canvas at 2x device pixel ratio and returns a PNG blob. */
export async function svgToPngBlob(svg: SVGSVGElement): Promise<Blob> {
  const rect = svg.getBoundingClientRect();
  const viewBoxWidth = svg.viewBox?.baseVal?.width;
  const viewBoxHeight = svg.viewBox?.baseVal?.height;
  const width = rect.width || viewBoxWidth || 300;
  const height = rect.height || viewBoxHeight || 150;
  const scale = 2 * (window.devicePixelRatio || 1);
  const serialized = serializeSvgForImage(svg);
  const svgUrl = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(serialized)}`;

  const image = new Image();
  const loaded = new Promise<void>((resolve, reject) => {
    image.onload = () => resolve();
    image.onerror = () => reject(new Error("svg image failed to load"));
  });
  image.src = svgUrl;
  await loaded;

  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const context = canvas.getContext("2d");
  if (!context) throw new Error("canvas 2d context unavailable");
  context.drawImage(image, 0, 0, canvas.width, canvas.height);

  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("canvas toBlob failed"))), "image/png");
  });
}
