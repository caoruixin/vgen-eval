import { useCallback, useState } from "react";

// Hosted demo: the visitor's own Tokenbay key lives only in this browser. sessionStorage by default (gone when
// the tab closes), localStorage when they tick "remember". The server keeps it in memory only while clips run.
const STORAGE_KEY = "vgen.tokenbay_key";
let memoryKey = ""; // fallback when storage is blocked (private windows, strict settings)

function read(): { key: string; remember: boolean } {
  try {
    const kept = localStorage.getItem(STORAGE_KEY);
    if (kept) return { key: kept, remember: true };
    return { key: sessionStorage.getItem(STORAGE_KEY) ?? memoryKey, remember: false };
  } catch {
    return { key: memoryKey, remember: false };
  }
}

export function storedKey(): string {
  return read().key;
}

export function saveKey(key: string, remember: boolean): void {
  memoryKey = key;
  try {
    localStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(STORAGE_KEY);
    if (key) (remember ? localStorage : sessionStorage).setItem(STORAGE_KEY, key);
  } catch {
    /* storage blocked: memoryKey keeps it for this page */
  }
}

export function useApiKey() {
  const [state, setState] = useState(read);
  const save = useCallback((key: string, remember: boolean) => {
    saveKey(key, remember);
    setState({ key, remember });
  }, []);
  const forget = useCallback(() => {
    saveKey("", false);
    setState({ key: "", remember: false });
  }, []);
  return { ...state, save, forget };
}
