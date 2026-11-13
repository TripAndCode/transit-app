export class I18nSetError extends Error {}

export function serialize(data: unknown): string;

export function parseArgv(argv: string[]): {
  positionals: string[];
  file: string | null;
  deleteKey: string | null;
  localesDir: string | null;
};

export const USAGE: string;

export function run(argv: string[]): string[];
