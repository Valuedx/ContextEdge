"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";

import {
  readStoredTheme,
  resolveDarkClass,
  type ThemeMode,
  writeStoredTheme,
} from "@/lib/theme-storage";

type ThemeContextValue = {
  theme: ThemeMode;
  setTheme: (mode: ThemeMode) => void;
  /** Whether the document is currently in dark appearance. */
  resolvedIsDark: boolean;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

const DEFAULT_THEME: ThemeMode = "dark";

function useResolvedIsDark(theme: ThemeMode): boolean {
  return useSyncExternalStore(
    (onStoreChange) => {
      if (theme === "light" || theme === "dark") return () => {};
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      mq.addEventListener("change", onStoreChange);
      return () => mq.removeEventListener("change", onStoreChange);
    },
    () => resolveDarkClass(theme),
    () => true
  );
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<ThemeMode>(DEFAULT_THEME);

  useLayoutEffect(() => {
    // Sync React state with localStorage after mount; inline bootstrap already set `html.dark`.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional one-shot hydration sync
    setThemeState(readStoredTheme() ?? DEFAULT_THEME);
  }, []);

  const resolvedIsDark = useResolvedIsDark(theme);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", resolvedIsDark);
  }, [resolvedIsDark]);

  const setTheme = useCallback((mode: ThemeMode) => {
    writeStoredTheme(mode);
    setThemeState(mode);
    document.documentElement.classList.toggle("dark", resolveDarkClass(mode));
  }, []);

  const value = useMemo(
    () => ({ theme, setTheme, resolvedIsDark }),
    [theme, setTheme, resolvedIsDark]
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within ThemeProvider");
  }
  return ctx;
}
