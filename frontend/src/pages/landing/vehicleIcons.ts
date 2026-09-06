// Vehicle glyphs for the landing hero's schematic city scene, vendored from
// the Maki icon set (https://github.com/mapbox/maki, CC0 / public domain
// dedication -- no attribution required). Each `d` string is copied
// verbatim from that repository's `icons/{bus,rail,rail-light}.svg`, all on
// a native `viewBox="0 0 15 15"` (see `MAKI_VIEWBOX_SIZE`).

export type VehicleMode = "bus" | "train" | "tram";

/** Side length of the square viewBox every Maki icon path below is drawn
 *  against (`0 0 15 15`) -- callers scale/translate by this to map the
 *  glyph into whatever on-screen size they need. */
export const MAKI_VIEWBOX_SIZE = 15;

export const VEHICLE_ICON_PATHS: Record<VehicleMode, string> = {
  bus: "M2 3C2 1.9 2.9 1 4 1H11C12.1 1 13 1.9 13 3V11C13 12 12 12 12 12V13C12 13.55 11.55 14 11 14C10.45 14 10 13.55 10 13V12H5V13C5 13.55 4.55 14 4 14C3.45 14 3 13.55 3 13V12C2 12 2 11 2 11V3ZM3.5 4C3.22 4 3 4.22 3 4.5V7.5C3 7.78 3.22 8 3.5 8H11.5C11.78 8 12 7.78 12 7.5V4.5C12 4.22 11.78 4 11.5 4H3.5ZM4 9C3.45 9 3 9.45 3 10C3 10.55 3.45 11 4 11C4.55 11 5 10.55 5 10C5 9.45 4.55 9 4 9ZM11 9C10.45 9 10 9.45 10 10C10 10.55 10.45 11 11 11C11.55 11 12 10.55 12 10C12 9.45 11.55 9 11 9ZM4 2.5C4 2.78 4.22 3 4.5 3H10.5C10.78 3 11 2.78 11 2.5C11 2.22 10.78 2 10.5 2H4.5C4.22 2 4 2.22 4 2.5Z",
  train:
    "M 3 1 C 2.4477 1 2 1.4477 2 2 L 2 10 C 2 10.5523 2.4477 11 3 11 L 12 11 C 12.5523 11 13 10.5523 13 10 L 13 2 C 13 1.4477 12.5523 1 12 1 L 3 1 z M 5.75 1.5 L 5.7597656 1.5 L 9.2597656 1.5 C 9.3978656 1.5 9.5097656 1.6119 9.5097656 1.75 C 9.5097656 1.8881 9.3978656 2 9.2597656 2 L 5.75 2 C 5.6119 2 5.5 1.8881 5.5 1.75 C 5.5 1.6119 5.6119 1.5 5.75 1.5 z M 3.5 3 L 7 3 L 7 7 L 3.5 7 C 3.2239 7 3 6.7761 3 6.5 L 3 3.5 C 3 3.2239 3.2239 3 3.5 3 z M 8 3 L 11.5 3 C 11.7761 3 12 3.2239 12 3.5 L 12 6.5 C 12 6.7761 11.7761 7 11.5 7 L 8 7 L 8 3 z M 5 8 C 5.5523 8 6 8.4477 6 9 C 6 9.5523 5.5523 10 5 10 C 4.4477 10 4 9.5523 4 9 C 4 8.4477 4.4477 8 5 8 z M 10 8 C 10.5523 8 11 8.4477 11 9 C 11 9.5523 10.5523 10 10 10 C 9.4477 10 9 9.5523 9 9 C 9 8.4477 9.4477 8 10 8 z M 10.445312 11.994141 C 10.380597 11.999652 10.314981 12.018581 10.253906 12.050781 C 10.030606 12.168381 9.9302313 12.433922 10.019531 12.669922 L 10.189453 13 L 4.8105469 13 L 4.9394531 12.730469 C 5.0371531 12.472169 4.9067375 12.183637 4.6484375 12.085938 C 4.4124375 11.996738 4.1468969 12.097113 4.0292969 12.320312 L 3.0292969 14.320312 C 3.0080969 14.377912 2.9986 14.4387 3 14.5 C 3 14.7761 3.2239 15 3.5 15 C 3.6802 14.999 3.8450875 14.899434 3.9296875 14.740234 L 3.9296875 14.689453 L 4 14.689453 L 4.3105469 14 L 10.689453 14 L 11 14.689453 L 11 14.740234 C 11.0846 14.899434 11.249488 14.999 11.429688 15 C 11.705787 15 11.929688 14.7761 11.929688 14.5 C 11.949587 14.4212 11.949587 14.338566 11.929688 14.259766 L 10.929688 12.259766 C 10.833163 12.076541 10.639459 11.977608 10.445312 11.994141 z",
  tram: "M5.5,0C5,0,5,0.5,5,0.5v1C5,1.777,5.223,2,5.5,2S6,1.777,6,1.5V1h1v2H6c0,0-2,0-2,2v3c0,3,3,3,3,3h1 c0,0,3,0,3-3V5c0-2-2-2-2-2H8V1h1v0.5C9,1.777,9.223,2,9.5,2S10,1.777,10,1.5v-1C10,0,9.5,0,9.5,0H5.5z M7.5,4l2.0449,0.7734L10,6.5 C10.1316,7,9.5,7,9.5,7h-4c0,0-0.6316,0-0.5-0.5l0.4551-1.7266L7.5,4z M7.5,8C7.7761,8,8,8.2239,8,8.5S7.7761,9,7.5,9 S7,8.7761,7,8.5S7.2239,8,7.5,8z M4.125,12L3,15h1.5l0.375-1h5.25l0.375,1H12l-1.125-3h-1.5l0.375,1h-4.5l0.375-1H4.125z",
};

type PathCommand = { type: string; args: number[] };

// Every Maki icon this scene uses only needs this subset of the SVG path
// mini-language: moveto/lineto/horizontal/vertical (M/m L/l H/h V/v),
// absolute + relative cubic Beziers (C/c) and their smooth-shorthand
// reflection (S/s), and closepath (Z/z). No arcs or quadratics appear in
// any vendored `d` string, so this deliberately doesn't implement them.
const COMMAND_LETTERS = new Set(["M", "m", "L", "l", "H", "h", "V", "v", "C", "c", "S", "s", "Z", "z"]);
const NUMBER_RE = /-?\d*\.?\d+(?:[eE][-+]?\d+)?/g;

function tokenizePath(d: string): PathCommand[] {
  const commands: PathCommand[] = [];
  let i = 0;
  while (i < d.length) {
    const ch = d[i];
    if (!COMMAND_LETTERS.has(ch)) {
      i++;
      continue;
    }
    const type = ch;
    i++;
    let argsStr = "";
    while (i < d.length && !COMMAND_LETTERS.has(d[i])) {
      argsStr += d[i];
      i++;
    }
    const args = (argsStr.match(NUMBER_RE) ?? []).map(Number);
    commands.push({ type, args });
  }
  return commands;
}

/** Replays a Maki `d` string's path commands against a canvas context that
 *  is already positioned/scaled for the glyph (translate + scale to map the
 *  native 0..15 viewBox onto the desired on-screen size), issuing
 *  `moveTo`/`lineTo`/`bezierCurveTo`/`closePath` calls -- deliberately not
 *  `Path2D` + `ctx.fill(path)`, since `Path2D` has no jsdom implementation
 *  and this repo's canvas tests run against a plain recording stub, not a
 *  real browser context. Caller owns `beginPath`/`fill`/`save`/`restore`. */
export function drawMakiPath(ctx: CanvasRenderingContext2D, d: string): void {
  replayPathCommands(ctx, tokenizePath(d));
}

function replayPathCommands(ctx: CanvasRenderingContext2D, commands: PathCommand[]): void {
  let x = 0;
  let y = 0;
  let startX = 0;
  let startY = 0;
  // Reflection state for S/s: only valid immediately after a C/c/S/s: the
  // implicit first control point mirrors the previous curve's second
  // control point through the current point; otherwise it equals the
  // current point (no reflection).
  let prevCtrlX = 0;
  let prevCtrlY = 0;
  let prevWasCubic = false;

  for (const { type, args } of commands) {
    switch (type) {
      case "M":
      case "m": {
        const relative = type === "m";
        for (let k = 0; k < args.length; k += 2) {
          x = relative ? x + args[k] : args[k];
          y = relative ? y + args[k + 1] : args[k + 1];
          if (k === 0) {
            ctx.moveTo(x, y);
            startX = x;
            startY = y;
          } else {
            // A moveto's subsequent coordinate pairs are implicit linetos.
            ctx.lineTo(x, y);
          }
        }
        prevWasCubic = false;
        break;
      }
      case "L":
      case "l": {
        const relative = type === "l";
        for (let k = 0; k < args.length; k += 2) {
          x = relative ? x + args[k] : args[k];
          y = relative ? y + args[k + 1] : args[k + 1];
          ctx.lineTo(x, y);
        }
        prevWasCubic = false;
        break;
      }
      case "H":
      case "h": {
        const relative = type === "h";
        for (const v of args) {
          x = relative ? x + v : v;
          ctx.lineTo(x, y);
        }
        prevWasCubic = false;
        break;
      }
      case "V":
      case "v": {
        const relative = type === "v";
        for (const v of args) {
          y = relative ? y + v : v;
          ctx.lineTo(x, y);
        }
        prevWasCubic = false;
        break;
      }
      case "C":
      case "c": {
        const relative = type === "c";
        for (let k = 0; k < args.length; k += 6) {
          const x1 = relative ? x + args[k] : args[k];
          const y1 = relative ? y + args[k + 1] : args[k + 1];
          const x2 = relative ? x + args[k + 2] : args[k + 2];
          const y2 = relative ? y + args[k + 3] : args[k + 3];
          const ex = relative ? x + args[k + 4] : args[k + 4];
          const ey = relative ? y + args[k + 5] : args[k + 5];
          ctx.bezierCurveTo(x1, y1, x2, y2, ex, ey);
          prevCtrlX = x2;
          prevCtrlY = y2;
          x = ex;
          y = ey;
          prevWasCubic = true;
        }
        break;
      }
      case "S":
      case "s": {
        const relative = type === "s";
        for (let k = 0; k < args.length; k += 4) {
          const x1 = prevWasCubic ? 2 * x - prevCtrlX : x;
          const y1 = prevWasCubic ? 2 * y - prevCtrlY : y;
          const x2 = relative ? x + args[k] : args[k];
          const y2 = relative ? y + args[k + 1] : args[k + 1];
          const ex = relative ? x + args[k + 2] : args[k + 2];
          const ey = relative ? y + args[k + 3] : args[k + 3];
          ctx.bezierCurveTo(x1, y1, x2, y2, ex, ey);
          prevCtrlX = x2;
          prevCtrlY = y2;
          x = ex;
          y = ey;
          prevWasCubic = true;
        }
        break;
      }
      case "Z":
      case "z":
        ctx.closePath();
        x = startX;
        y = startY;
        prevWasCubic = false;
        break;
      default:
        break;
    }
  }
}

// Each vendored icon's `d` string is fixed at build time -- the per-frame
// vehicle-draw loop calls `drawVehicleIcon` for every vehicle on every
// `requestAnimationFrame` tick, so tokenizing the same three strings once
// here (instead of on every call) avoids re-parsing static path data on a
// hot animation path.
const PARSED_VEHICLE_ICON_PATHS: Record<VehicleMode, PathCommand[]> = Object.fromEntries(
  (Object.entries(VEHICLE_ICON_PATHS) as [VehicleMode, string][]).map(([mode, d]) => [mode, tokenizePath(d)]),
) as Record<VehicleMode, PathCommand[]>;

/** Replays one of the three vendored vehicle-mode glyphs from its pre-parsed
 *  command array -- the render loop's per-frame entry point, so it never
 *  re-tokenizes the static `d` string on the hot path. Caller owns
 *  `beginPath`/`fill`/`save`/`restore`, same as `drawMakiPath`. */
export function drawVehicleIcon(ctx: CanvasRenderingContext2D, mode: VehicleMode): void {
  replayPathCommands(ctx, PARSED_VEHICLE_ICON_PATHS[mode]);
}
