#!/usr/bin/env node
// Adds, updates or deletes one i18n key in both frontend/src/i18n/locales/en.json
// and ja.json without an agent reading either 2,000+ line file first.
//
// Usage:
//   node scripts/i18n-set.mjs <dotted.key> "<en text>" "<ja text>"
//   node scripts/i18n-set.mjs --file <path/to/strings.json>
//   node scripts/i18n-set.mjs --delete <dotted.key>
//   (any form also takes --locales-dir <dir>, for tests against temp copies)
//
// --file takes a JSON object of { "dotted.key": { "en": "...", "ja": "..." } }
// so one feature's strings land in a single command.
//
// Both locale files are written, or neither: every requested change is
// applied to in-memory copies of both locale objects first, and only
// written to disk once the whole batch succeeds. A refusal (see
// I18nSetError below) aborts before anything is read from disk.
//
// FORMATTING: the locale files are hand-maintained with 2-space indents and
// insertion-order keys (not sorted), which `JSON.parse` and `JSON.stringify`
// already preserve. The one byte-level quirk is a non-breaking space
// (U+00A0): JSON.stringify always emits it as a literal character, so
// `serialize` below canonicalizes it to a backslash-u escape afterward,
// making every occurrence byte-identical however the source first wrote it.
//
// Deleting a key that leaves its parent object with no other children
// prunes that parent too, recursively, so `--delete` cannot litter the file
// with empty `{}` sections. It never prunes the file's root object.

import { readFileSync, writeFileSync, renameSync, unlinkSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export class I18nSetError extends Error {}

export const USAGE = [
  'npm run i18n:set -- <dotted.key> "<en text>" "<ja text>"',
  "npm run i18n:set -- --file <path/to/strings.json>",
  "npm run i18n:set -- --delete <dotted.key>",
].join("\n");

function isPlainObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Canonical on-disk form for a parsed locale object: 2-space indented JSON
 *  with every non-breaking space written as a backslash-u escape. */
export function serialize(data) {
  return `${JSON.stringify(data, null, 2).replace(/\u00a0/g, "\\u00a0")}\n`;
}

function splitPath(dottedKey) {
  if (typeof dottedKey !== "string" || dottedKey.length === 0) {
    throw new I18nSetError(`invalid key: ${JSON.stringify(dottedKey)}`);
  }
  const parts = dottedKey.split(".");
  if (parts.some((part) => part.length === 0)) {
    throw new I18nSetError(`invalid key (empty segment): "${dottedKey}"`);
  }
  return parts;
}

function setKeyInObject(root, parts, value) {
  let node = root;
  for (let i = 0; i < parts.length - 1; i++) {
    const part = parts[i];
    if (!Object.hasOwn(node, part)) {
      const next = {};
      node[part] = next;
      node = next;
      continue;
    }
    const existing = node[part];
    if (!isPlainObject(existing)) {
      throw new I18nSetError(`refusing to create a nested key under non-object key "${parts.slice(0, i + 1).join(".")}"`);
    }
    node = existing;
  }
  const lastPart = parts[parts.length - 1];
  if (Object.hasOwn(node, lastPart) && isPlainObject(node[lastPart])) {
    throw new I18nSetError(`refusing to overwrite object key "${parts.join(".")}" with a string`);
  }
  node[lastPart] = value;
}

/** Deletes `parts` from `root`. Refuses to delete a key that currently
 *  holds an object (only a leaf can be deleted directly), but once a
 *  deletion empties its parent object, that now-empty parent is pruned too
 *  — recursively, stopping at the first still-non-empty ancestor or at the
 *  root, which is never pruned. Returns whether anything was deleted. */
function deleteKeyFromObject(root, parts) {
  let currentParts = parts;
  let allowObjectDelete = false;
  let deletedAny = false;
  while (currentParts.length > 0) {
    const parentParts = currentParts.slice(0, -1);
    const lastPart = currentParts[currentParts.length - 1];
    let parent = root;
    let missing = false;
    for (const part of parentParts) {
      if (!isPlainObject(parent) || !Object.hasOwn(parent, part)) {
        missing = true;
        break;
      }
      parent = parent[part];
    }
    if (missing || !isPlainObject(parent) || !Object.hasOwn(parent, lastPart)) break;
    const value = parent[lastPart];
    if (isPlainObject(value) && !allowObjectDelete) {
      throw new I18nSetError(`refusing to delete object key "${currentParts.join(".")}"`);
    }
    delete parent[lastPart];
    deletedAny = true;
    if (Object.keys(parent).length > 0) break;
    currentParts = parentParts;
    allowObjectDelete = true;
  }
  return deletedAny;
}

function isFlagValue(value) {
  return typeof value === "string" && value.length > 0 && !value.startsWith("--");
}

const FLAGS = new Map([
  ["--file", "file"],
  ["--delete", "deleteKey"],
  ["--locales-dir", "localesDir"],
]);

/** Splits `argv` into positionals and the three recognized flags. Throws
 *  `I18nSetError` on a flag whose value is missing or itself looks like a
 *  flag, before anything is read or written. */
export function parseArgv(argv) {
  const result = { positionals: [], file: null, deleteKey: null, localesDir: null };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const field = FLAGS.get(arg);
    if (field === undefined) {
      result.positionals.push(arg);
      continue;
    }
    const value = argv[i + 1];
    if (!isFlagValue(value)) {
      throw new I18nSetError(`${arg} needs a value. Usage:\n${USAGE}`);
    }
    result[field] = value;
    i++;
  }
  return result;
}

function readBatchFile(filePath) {
  const raw = readFileSync(path.resolve(filePath), "utf8");
  let batch;
  try {
    batch = JSON.parse(raw);
  } catch (err) {
    throw new I18nSetError(`--file is not valid JSON: ${err.message}`);
  }
  if (!isPlainObject(batch)) {
    throw new I18nSetError("--file's JSON root must be an object of { dotted.key: { en, ja } }");
  }
  const entries = Object.entries(batch);
  if (entries.length === 0) {
    throw new I18nSetError("--file's JSON object has no entries");
  }
  for (const [key, entry] of entries) {
    if (!isPlainObject(entry) || typeof entry.en !== "string" || typeof entry.ja !== "string") {
      throw new I18nSetError(`batch entry "${key}" must be { "en": string, "ja": string }`);
    }
  }
  return batch;
}

function writeLocalesAtomic(files) {
  const toWrite = files.filter((f) => f.newText !== f.oldText);
  if (toWrite.length === 0) return;
  const staged = toWrite.map((f) => ({
    ...f,
    tmpPath: `${f.path}.tmp-${process.pid}-${Math.random().toString(36).slice(2)}`,
  }));
  try {
    for (const f of staged) writeFileSync(f.tmpPath, f.newText, "utf8");
    for (const f of staged) renameSync(f.tmpPath, f.path);
  } catch (err) {
    for (const f of staged) {
      try {
        unlinkSync(f.tmpPath);
      } catch {
        // already renamed, or never created; nothing to clean up
      }
    }
    throw err;
  }
}

/** Runs the CLI against the locale files under `--locales-dir` (default:
 *  this repo's frontend/src/i18n/locales/). Returns the list of per-key
 *  summary lines on success; throws `I18nSetError` on a refusal or a usage
 *  error, before anything is written. */
export function run(argv) {
  const { positionals, file, deleteKey, localesDir } = parseArgv(argv);
  const dir = localesDir
    ? path.resolve(localesDir)
    : fileURLToPath(new URL("../src/i18n/locales/", import.meta.url));
  const enPath = path.join(dir, "en.json");
  const jaPath = path.join(dir, "ja.json");

  if (deleteKey !== null) {
    if (file !== null || positionals.length > 0) {
      throw new I18nSetError(`--delete takes no other arguments. Usage:\n${USAGE}`);
    }
  } else if (file !== null) {
    if (positionals.length > 0) {
      throw new I18nSetError(`--file takes no other positional arguments. Usage:\n${USAGE}`);
    }
  } else if (positionals.length !== 3) {
    throw new I18nSetError(`expected exactly 3 arguments. Usage:\n${USAGE}`);
  }

  const enRaw = readFileSync(enPath, "utf8");
  const jaRaw = readFileSync(jaPath, "utf8");
  const en = JSON.parse(enRaw);
  const ja = JSON.parse(jaRaw);
  const summary = [];

  if (deleteKey !== null) {
    const parts = splitPath(deleteKey);
    const deletedEn = deleteKeyFromObject(en, parts);
    const deletedJa = deleteKeyFromObject(ja, parts);
    if (!deletedEn && !deletedJa) {
      throw new I18nSetError(`key not found in either locale: "${deleteKey}"`);
    }
    summary.push(`deleted ${deleteKey}`);
  } else if (file !== null) {
    const batch = readBatchFile(file);
    for (const [key, entry] of Object.entries(batch)) {
      const parts = splitPath(key);
      setKeyInObject(en, parts, entry.en);
      setKeyInObject(ja, parts, entry.ja);
      summary.push(`set ${key}`);
    }
  } else {
    const [key, enValue, jaValue] = positionals;
    const parts = splitPath(key);
    setKeyInObject(en, parts, enValue);
    setKeyInObject(ja, parts, jaValue);
    summary.push(`set ${key}`);
  }

  writeLocalesAtomic([
    { path: enPath, oldText: enRaw, newText: serialize(en) },
    { path: jaPath, oldText: jaRaw, newText: serialize(ja) },
  ]);
  return summary;
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  try {
    for (const line of run(process.argv.slice(2))) console.log(line);
  } catch (err) {
    console.error(err instanceof I18nSetError ? err.message : (err?.stack ?? String(err)));
    process.exit(1);
  }
}
