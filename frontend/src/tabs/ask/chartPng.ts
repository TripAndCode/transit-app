/**
 * Rasterizes an inline SVG chart to a PNG and triggers a browser download.
 * No new dependency: serialize → data-URL `<img>` → `<canvas>` → `toBlob`,
 * all native. Silently does nothing if the canvas 2D context is
 * unavailable (e.g. a locked-down embedding) rather than throwing — a
 * failed "save image" click should never break the page around it.
 */
export function exportSvgAsPng(svg: SVGSVGElement, filename: string): void {
  const rect = svg.getBoundingClientRect();
  const width = rect.width || Number(svg.getAttribute("width")) || 800;
  const height = rect.height || Number(svg.getAttribute("height")) || 400;

  const xml = new XMLSerializer().serializeToString(svg);
  const svgBlob = new Blob([xml], { type: "image/svg+xml;charset=utf-8" });
  const svgUrl = URL.createObjectURL(svgBlob);

  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    URL.revokeObjectURL(svgUrl);
    if (!ctx) return;
    ctx.drawImage(img, 0, 0, width, height);
    canvas.toBlob((blob) => {
      if (!blob) return;
      const link = document.createElement("a");
      const pngUrl = URL.createObjectURL(blob);
      link.href = pngUrl;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(pngUrl);
    }, "image/png");
  };
  img.src = svgUrl;
}
