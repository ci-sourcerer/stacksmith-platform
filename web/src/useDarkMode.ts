import { useEffect, useState } from "react";

const STORAGE_KEY = "stacksmith-theme";

function storedTheme(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch (error) {
    if (!(error instanceof DOMException)) throw error;
    return null;
  }
}

function getInitialDark(): boolean {
  if (typeof window === "undefined") return false;
  const stored = storedTheme();
  if (stored !== null) return stored === "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function useDarkMode(): [boolean, () => void] {
  const [dark, setDark] = useState<boolean>(() => getInitialDark());

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    function onSystemChange(e: MediaQueryListEvent) {
      if (storedTheme() === null) {
        setDark(e.matches);
      }
    }
    mq.addEventListener("change", onSystemChange);
    return () => mq.removeEventListener("change", onSystemChange);
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  function toggle() {
    setDark((prev: boolean) => {
      const next = !prev;
      try {
        localStorage.setItem(STORAGE_KEY, next ? "dark" : "light");
      } catch (error) {
        if (!(error instanceof DOMException)) throw error;
        // Keep the current theme usable when browser preferences are blocked.
      }
      return next;
    });
  }

  return [dark, toggle];
}
