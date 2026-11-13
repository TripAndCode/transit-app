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
// applied to both in-memory texts first, and only written to disk once the
// whole batch succeeds. A refusal (see I18nSetError below) aborts before any
// write.
//
// FORMATTING: the locale files are hand-maintained with 2-space indents and
// insertion-order keys (not sorted). A naive `JSON.parse` + `JSON.stringify`
// round-trip cannot reproduce them byte for byte: en.json mixes a literal
// U+00A0 in one string with a ` ` escape in two others, and
// `JSON.stringify` only ever produces the former. So this module never
// reserializes the whole file. It parses just enough structure (object
// spans, string spans) to splice a single change into the original text,
// leaving every untouched byte — including that mixed escaping — alone.
// Re-setting a key to its current decoded value is therefore a true no-op:
// the original source span is left untouched rather than regenerated.
//
// Deleting a key that leaves its parent object with no other children
// prunes that parent too, recursively, so `--delete` cannot litter the file
// with empty `{}` sections. It never prunes the file's root object.

import { readFileSync, writeFileSync, renameSync, unlinkSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const INDENT = "  ";

export class I18nSetError extends Error {}

/** Parses `text` into a tree of `{ type, start, end, ... }` nodes with
 *  source spans, so edits can splice the original text instead of
 *  regenerating it. Supports every JSON value kind; only "object" is a
 *  container for dotted-path purposes, everything else is a leaf. */
export function parseLocaleJson(text) {
  let i = 0;
  const n = text.length;

  function skipWs() {
    while (i < n && /\s/.test(text[i])) i++;
  }

  function parseString() {
    const start = i;
    i++; // opening quote
    while (i < n) {
      const ch = text[i];
      if (ch === "\\") {
        i += 2;
        continue;
      }
      if (ch === '"') {
        i++;
        break;
      }
      i++;
    }
    const end = i;
    const raw = text.slice(start, end);
    return { type: "string", value: JSON.parse(raw), raw, start, end };
  }

  function parseObject(depth) {
    const start = i;
    i++; // {
    const entries = [];
    skipWs();
    if (text[i] === "}") {
      i++;
      return { type: "object", entries, start, end: i, depth };
    }
    while (true) {
      skipWs();
      if (text[i] !== '"') throw new Error(`expected a key string at index ${i}`);
      const keyNode = parseString();
      skipWs();
      if (text[i] !== ":") throw new Error(`expected ':' at index ${i}`);
      i++;
      const value = parseValue(depth + 1);
      entries.push({ key: keyNode.value, keyNode, value });
      skipWs();
      if (text[i] === ",") {
        i++;
        continue;
      }
      if (text[i] === "}") {
        i++;
        break;
      }
      throw new Error(`expected ',' or '}' at index ${i}`);
    }
    return { type: "object", entries, start, end: i, depth };
  }

  function parseArray(depth) {
    const start = i;
    i++; // [
    const items = [];
    skipWs();
    if (text[i] === "]") {
      i++;
      return { type: "array", items, start, end: i };
    }
    while (true) {
      items.push(parseValue(depth));
      skipWs();
      if (text[i] === ",") {
        i++;
        continue;
      }
      if (text[i] === "]") {
        i++;
        break;
      }
      throw new Error(`expected ',' or ']' at index ${i}`);
    }
    return { type: "array", items, start, end: i };
  }

  function parseValue(depth) {
    skipWs();
    const c = text[i];
    if (c === "{") return parseObject(depth);
    if (c === "[") return parseArray(depth);
    if (c === '"') return parseString();
    if (text.startsWith("true", i)) {
      const start = i;
      i += 4;
      return { type: "literal", value: true, start, end: i };
    }
    if (text.startsWith("false", i)) {
      const start = i;
      i += 5;
      return { type: "literal", value: false, start, end: i };
    }
    if (text.startsWith("null", i)) {
      const start = i;
      i += 4;
      return { type: "literal", value: null, start, end: i };
    }
    const m = /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(i));
    if (m) {
      const start = i;
      i += m[0].length;
      return { type: "literal", value: Number(m[0]), start, end: i };
    }
    throw new Error(`unexpected token at index ${i}`);
  }

  const root = parseValue(0);
  skipWs();
  if (i !== n) throw new Error(`unexpected trailing content at index ${i}`);
  if (root.type !== "object") throw new Error("locale file's top level must be an object");
  return root;
}

export function splitPath(dottedKey) {
  if (typeof dottedKey !== "string" || dottedKey.length === 0) {
    throw new I18nSetError(`invalid key: ${JSON.stringify(dottedKey)}`);
  }
  const parts = dottedKey.split(".");
  if (parts.some((part) => part.length === 0)) {
    throw new I18nSetError(`invalid key (empty segment): "${dottedKey}"`);
  }
  return parts;
}

function applySplices(text, splices) {
  const sorted = [...splices].sort((a, b) => b.start - a.start);
  let out = text;
  for (const splice of sorted) {
    out = out.slice(0, splice.start) + splice.text + out.slice(splice.end);
  }
  return out;
}

function buildEntryText(parts, value, parentDepth) {
  const ownIndent = INDENT.repeat(parentDepth + 1);
  const [head, ...rest] = parts;
  const keyText = JSON.stringify(head);
  if (rest.length === 0) {
    return `${ownIndent}${keyText}: ${JSON.stringify(value)}`;
  }
  const inner = buildEntryText(rest, value, parentDepth + 1);
  return `${ownIndent}${keyText}: {\n${inner}\n${ownIndent}}`;
}

function buildCreateSplice(parentObjectNode, remainingParts, value) {
  const entryText = buildEntryText(remainingParts, value, parentObjectNode.depth);
  if (parentObjectNode.entries.length > 0) {
    const last = parentObjectNode.entries[parentObjectNode.entries.length - 1];
    return { start: last.value.end, end: last.value.end, text: `,\n${entryText}` };
  }
  const closeIndent = INDENT.repeat(parentObjectNode.depth);
  return { start: parentObjectNode.start + 1, end: parentObjectNode.end - 1, text: `\n${entryText}\n${closeIndent}` };
}

/** Walks `parts` down `tree` for a `set`. Returns one of:
 *  - `{ kind: "leaf", entry }` — the full path resolves to an existing
 *    string (or other non-object) leaf, ready to update;
 *  - `{ kind: "create", parentObjectNode, remainingParts }` — the path is
 *    only partly present; `remainingParts` (length >= 1) must be created
 *    fresh under `parentObjectNode`;
 *  - `{ kind: "conflict-object", path }` — the full path already holds an
 *    object (refuse to overwrite it with a string);
 *  - `{ kind: "conflict-leaf", path }` — an intermediate segment already
 *    holds a non-object (refuse to turn a string into an object). */
function locateForSet(tree, parts) {
  let current = tree;
  for (let i = 0; i < parts.length; i++) {
    const isLast = i === parts.length - 1;
    const entry = current.entries.find((e) => e.key === parts[i]);
    if (!entry) {
      return { kind: "create", parentObjectNode: current, remainingParts: parts.slice(i) };
    }
    if (isLast) {
      if (entry.value.type === "object") {
        return { kind: "conflict-object", path: parts.slice(0, i + 1) };
      }
      return { kind: "leaf", entry };
    }
    if (entry.value.type !== "object") {
      return { kind: "conflict-leaf", path: parts.slice(0, i + 1) };
    }
    current = entry.value;
  }
  throw new Error("unreachable");
}

export function setKeyInText(text, parts, value) {
  const tree = parseLocaleJson(text);
  const loc = locateForSet(tree, parts);
  if (loc.kind === "conflict-object") {
    throw new I18nSetError(`refusing to overwrite object key "${loc.path.join(".")}" with a string`);
  }
  if (loc.kind === "conflict-leaf") {
    throw new I18nSetError(`refusing to create a nested key under string key "${loc.path.join(".")}"`);
  }
  if (loc.kind === "leaf") {
    if (loc.entry.value.type === "string" && loc.entry.value.value === value) return text;
    const splice = { start: loc.entry.value.start, end: loc.entry.value.end, text: JSON.stringify(value) };
    return applySplices(text, [splice]);
  }
  const splice = buildCreateSplice(loc.parentObjectNode, loc.remainingParts, value);
  return applySplices(text, [splice]);
}

function lineStartOf(text, index) {
  const nl = text.lastIndexOf("\n", index - 1);
  return nl === -1 ? 0 : nl + 1;
}

function locateForDelete(tree, parts) {
  let current = tree;
  for (let i = 0; i < parts.length; i++) {
    const idx = current.entries.findIndex((e) => e.key === parts[i]);
    if (idx === -1) return { kind: "not-found" };
    const entry = current.entries[idx];
    const isLast = i === parts.length - 1;
    if (isLast) {
      return { kind: "found", parentObjectNode: current, entryIndex: idx, entryIsObject: entry.value.type === "object" };
    }
    if (entry.value.type !== "object") return { kind: "not-found" };
    current = entry.value;
  }
  throw new Error("unreachable");
}

function buildDeleteSplice(text, parentObjectNode, entryIndex) {
  const entries = parentObjectNode.entries;
  const entry = entries[entryIndex];
  if (entries.length === 1) {
    return { start: parentObjectNode.start + 1, end: parentObjectNode.end - 1, text: "" };
  }
  if (entryIndex === entries.length - 1) {
    const prev = entries[entryIndex - 1];
    return { start: prev.value.end, end: entry.value.end, text: "" };
  }
  const next = entries[entryIndex + 1];
  return { start: lineStartOf(text, entry.keyNode.start), end: lineStartOf(text, next.keyNode.start), text: "" };
}

/** Deletes `parts` from `text`. Refuses to delete a key that currently
 *  holds an object (only a leaf can be deleted directly), but once a
 *  deletion empties its parent object, that now-empty parent is pruned
 *  too — recursively, stopping at the first still-non-empty ancestor or at
 *  the file's root, which is never pruned. Returns `text` unchanged if the
 *  key is not present. */
export function deleteKeyInText(text, parts) {
  let current = text;
  let currentParts = parts;
  let allowObject = false;
  while (currentParts.length > 0) {
    const tree = parseLocaleJson(current);
    const loc = locateForDelete(tree, currentParts);
    if (loc.kind === "not-found") break;
    if (loc.entryIsObject && !allowObject) {
      throw new I18nSetError(`refusing to delete object key "${currentParts.join(".")}"`);
    }
    const parentHadOnlyThisEntry = loc.parentObjectNode.entries.length === 1;
    const splice = buildDeleteSplice(current, loc.parentObjectNode, loc.entryIndex);
    current = applySplices(current, [splice]);
    if (currentParts.length === 1 || !parentHadOnlyThisEntry) break;
    currentParts = currentParts.slice(0, -1);
    allowObject = true; // now pruning an ancestor object that this deletion just emptied
  }
  return current;
}

function parseArgv(argv) {
  const positionals = [];
  let file = null;
  let deleteKey = null;
  let localesDir = null;
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--file") {
      file = argv[++i];
      continue;
    }
    if (arg === "--delete") {
      deleteKey = argv[++i];
      continue;
    }
    if (arg === "--locales-dir") {
      localesDir = argv[++i];
      continue;
    }
    positionals.push(arg);
  }
  return { positionals, file, deleteKey, localesDir };
}

export const USAGE = 'npm run i18n:set -- <dotted.key> "<en text>" "<ja text>"';

function writeFileAtomic(filePath, contents) {
  const tmpPath = `${filePath}.tmp-${process.pid}`;
  writeFileSync(tmpPath, contents, "utf8");
  try {
    renameSync(tmpPath, filePath);
  } catch (err) {
    unlinkSync(tmpPath);
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

  let enText = readFileSync(enPath, "utf8");
  let jaText = readFileSync(jaPath, "utf8");
  const summary = [];

  if (deleteKey !== null) {
    if (file !== null || positionals.length > 0) {
      throw new I18nSetError(`--delete takes no other arguments. Usage: ${USAGE}`);
    }
    const parts = splitPath(deleteKey);
    const newEn = deleteKeyInText(enText, parts);
    const newJa = deleteKeyInText(jaText, parts);
    if (newEn === enText && newJa === jaText) {
      throw new I18nSetError(`key not found in either locale: "${deleteKey}"`);
    }
    enText = newEn;
    jaText = newJa;
    summary.push(`deleted ${deleteKey}`);
  } else if (file !== null) {
    if (positionals.length > 0) {
      throw new I18nSetError(`--file takes no other positional arguments. Usage: ${USAGE}`);
    }
    const batch = JSON.parse(readFileSync(path.resolve(file), "utf8"));
    for (const [key, entry] of Object.entries(batch)) {
      if (!entry || typeof entry.en !== "string" || typeof entry.ja !== "string") {
        throw new I18nSetError(`batch entry "${key}" must be { "en": string, "ja": string }`);
      }
      const parts = splitPath(key);
      enText = setKeyInText(enText, parts, entry.en);
      jaText = setKeyInText(jaText, parts, entry.ja);
      summary.push(`set ${key}`);
    }
    if (summary.length === 0) {
      throw new I18nSetError("--file's JSON object has no entries");
    }
  } else {
    if (positionals.length !== 3) {
      throw new I18nSetError(`expected exactly 3 arguments. Usage: ${USAGE}`);
    }
    const [key, en, ja] = positionals;
    const parts = splitPath(key);
    enText = setKeyInText(enText, parts, en);
    jaText = setKeyInText(jaText, parts, ja);
    summary.push(`set ${key}`);
  }

  writeFileAtomic(enPath, enText);
  writeFileAtomic(jaPath, jaText);
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
