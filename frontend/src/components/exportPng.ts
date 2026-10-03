/** Renders an SVG to a canvas at 2x device pixel ratio and returns a PNG blob. */
export async function svgToPngBlob(svg: SVGSVGElement): Promise<Blob> {
  const rect = svg.getBoundingClientRect();
  const viewBoxWidth = svg.viewBox?.baseVal?.width;
  const viewBoxHeight = svg.viewBox?.baseVal?.height;
  const width = rect.width || viewBoxWidth || 300;
  const height = rect.height || viewBoxHeight || 150;
  const scale = 2 * (window.devicePixelRatio || 1);
  const serialized = new XMLSerializer().serializeToString(svg);
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
