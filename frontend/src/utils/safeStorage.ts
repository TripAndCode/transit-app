/**
 * Web storage that a refusing browser cannot crash. Where site data is
 * blocked, every access throws, reaching `window.localStorage` included, and
 * a full store throws on write. The store is looked up on each call, inside
 * the guard, so a refusal at any point reads as "nothing stored" and a
 * refused write reports false. eslint.config.js bans touching storage
 * anywhere else.
 */

type StorageArea = "localStorage" | "sessionStorage";

/** `access(store)`, or `blocked` when that throws: the browser refused the
 *  store, or `access` failed on what it read (corrupt JSON, say). For a caller
 *  that must tell a refused store apart from an empty one. */
export function withStorage<T>(area: StorageArea, access: (storage: Storage) => T, blocked: T): T {
  try {
    return access(area === "localStorage" ? localStorage : sessionStorage);
  } catch {
    return blocked;
  }
}

/** The stored value; null when unset or when the store is refused. */
export function readStorage(area: StorageArea, key: string): string | null {
  return withStorage(area, (storage) => storage.getItem(key), null);
}

/** Whether `value` was stored. */
export function writeStorage(area: StorageArea, key: string, value: string): boolean {
  return withStorage(
    area,
    (storage) => {
      storage.setItem(key, value);
      return true;
    },
    false,
  );
}

/** Whether `key` is now gone from the store. */
export function removeStorage(area: StorageArea, key: string): boolean {
  return withStorage(
    area,
    (storage) => {
      storage.removeItem(key);
      return true;
    },
    false,
  );
}
