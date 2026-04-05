export const THEME_STORAGE_KEY = "contextedge-theme";

export type ThemeMode = "light" | "dark" | "system";

export function readStoredTheme(): ThemeMode | null {
  if (typeof window === "undefined") return null;
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    /* ignore */
  }
  return null;
}

export function writeStoredTheme(mode: ThemeMode) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, mode);
  } catch {
    /* ignore */
  }
}

export function resolveDarkClass(mode: ThemeMode): boolean {
  if (mode === "light") return false;
  if (mode === "dark") return true;
  if (typeof window === "undefined") return true;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Inline bootstrap for `beforeInteractive` script (no newlines for compactness). */
export const THEME_BOOTSTRAP_SCRIPT = `(()=>{try{var k="${THEME_STORAGE_KEY}";var t=localStorage.getItem(k);var dark=true;if(t==="light")dark=false;else if(t==="dark")dark=true;else if(t==="system")dark=matchMedia("(prefers-color-scheme: dark)").matches;document.documentElement.classList.toggle("dark",dark);}catch(e){document.documentElement.classList.add("dark");}})();`;
