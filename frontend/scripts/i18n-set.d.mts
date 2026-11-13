export class I18nSetError extends Error {}

export function parseLocaleJson(text: string): {
  type: "object";
  entries: Array<{ key: string; keyNode: unknown; value: unknown }>;
  start: number;
  end: number;
  depth: number;
};

export function splitPath(dottedKey: string): string[];

export function setKeyInText(text: string, parts: string[], value: string): string;

export function deleteKeyInText(text: string, parts: string[]): string;

export const USAGE: string;

export function run(argv: string[]): string[];
