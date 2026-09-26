import { useEffect, useState } from "react";

export type ThemePref = "system" | "light" | "dark";
const KEY = "tradebot.theme";

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(pref: ThemePref) {
  const root = document.documentElement;
  if (pref === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", pref);
  try {
    if (pref === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
  } catch {
    /* storage unavailable */
  }
  window.dispatchEvent(new Event("tradebot-theme"));
}

export function initTheme() {
  const p = readPref();
  if (p !== "system") document.documentElement.setAttribute("data-theme", p);
}

export function useThemePref(): [ThemePref, (p: ThemePref) => void] {
  const [pref, setPref] = useState<ThemePref>(readPref());
  return [
    pref,
    (p) => {
      applyTheme(p);
      setPref(p);
    },
  ];
}

/** Read resolved CSS custom properties; re-evaluates when the theme or OS scheme changes. */
export function useTokens(names: string[]): Record<string, string> {
  const read = () => {
    const cs = getComputedStyle(document.documentElement);
    return Object.fromEntries(names.map((n) => [n, cs.getPropertyValue(n).trim()]));
  };
  const [tokens, setTokens] = useState<Record<string, string>>(read);
  useEffect(() => {
    const update = () => setTokens(read());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", update);
    window.addEventListener("tradebot-theme", update);
    return () => {
      mq.removeEventListener("change", update);
      window.removeEventListener("tradebot-theme", update);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [names.join(",")]);
  return tokens;
}

export function isDark(): boolean {
  const t = document.documentElement.getAttribute("data-theme");
  if (t === "dark") return true;
  if (t === "light") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}
