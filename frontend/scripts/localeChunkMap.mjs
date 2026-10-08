// Build plugin for index.html's locale preload: fills the pre-mount script's
// placeholder with each language's hashed chunk URL. It fails the build when
// a locale file has no chunk of its own, because a static import folds that
// language into a chunk every visitor downloads.

import { readdirSync } from "node:fs";
import { join } from "node:path";

const LOCALE_CHUNKS_PLACEHOLDER = "/*__LOCALE_CHUNKS__*/ {}";

export function localeChunkMap() {
  let localesDir = "";
  let base = "/";
  return {
    name: "locale-chunk-map",
    apply: "build",
    configResolved(config) {
      localesDir = join(config.root, "src/i18n/locales");
      base = config.base;
    },
    transformIndexHtml: {
      order: "post",
      handler(html, { bundle }) {
        const chunks = {};
        for (const output of Object.values(bundle ?? {})) {
          if (output.type !== "chunk" || !output.facadeModuleId?.startsWith(`${localesDir}/`)) continue;
          chunks[output.facadeModuleId.slice(localesDir.length + 1).replace(/\.json$/, "")] = base + output.fileName;
        }
        const missing = readdirSync(localesDir)
          .filter((file) => file.endsWith(".json"))
          .map((file) => file.replace(/\.json$/, ""))
          .filter((locale) => !(locale in chunks));
        if (missing.length > 0) {
          throw new Error(
            `locale-chunk-map: no chunk of its own for ${missing.join(", ")}. Load locale JSON only ` +
              "through the dynamic loaders in src/i18n/index.ts, never with a static import.",
          );
        }
        if (html.split(LOCALE_CHUNKS_PLACEHOLDER).length !== 2) {
          throw new Error(`locale-chunk-map: index.html must contain ${LOCALE_CHUNKS_PLACEHOLDER} exactly once.`);
        }
        return html.replace(LOCALE_CHUNKS_PLACEHOLDER, () => JSON.stringify(chunks));
      },
    },
  };
}
