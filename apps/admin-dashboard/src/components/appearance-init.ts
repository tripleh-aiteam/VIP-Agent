// Appearance preferences — the shared, framework-free half of the theming
// system. Kept in a plain module (NO "use client") for two reasons:
//   1. the server component layout.tsx imports APPEARANCE_INIT_SCRIPT to inject
//      it into <head>, and a client module's exports would arrive as client
//      references instead of the actual string;
//   2. the client provider (appearance.tsx) imports the same keys/defaults, so
//      the pre-paint script and React can never drift apart.
//
// Everything is stored per-browser in localStorage and applied to <html> as a
// class (.dark) + data attributes that globals.css keys off of.

export type Theme = "light" | "dark" | "auto";
export type Background = "default" | "paper" | "warm" | "cool";
export type Accent = "blue" | "purple" | "green" | "orange" | "rose" | "graphite";
export type UiScale = "sm" | "md" | "lg" | "xl";
export type Motion = "full" | "reduced";

export interface Appearance {
  theme: Theme;
  background: Background;
  accent: Accent;
  scale: UiScale;
  motion: Motion;
}

export const APPEARANCE_DEFAULTS: Appearance = {
  theme: "light", // white by default — the boss's preference since day one
  background: "default",
  accent: "blue",
  scale: "md",
  motion: "full",
};

// localStorage keys. "vip-theme" predates this module and used to hold only
// "light" | "dark"; both of those are still valid values, so old browsers
// upgrade silently.
export const APPEARANCE_KEYS: Record<keyof Appearance, string> = {
  theme: "vip-theme",
  background: "vip-background",
  accent: "vip-accent",
  scale: "vip-ui-scale",
  motion: "vip-motion",
};

export const THEME_IDS: Theme[] = ["light", "dark", "auto"];
export const BACKGROUND_IDS: Background[] = ["default", "paper", "warm", "cool"];
export const ACCENT_IDS: Accent[] = ["blue", "purple", "green", "orange", "rose", "graphite"];
export const SCALE_IDS: UiScale[] = ["sm", "md", "lg", "xl"];
export const MOTION_IDS: Motion[] = ["full", "reduced"];

/**
 * Runs in <head> before the first paint, so a dark-mode user never sees a
 * white flash and a 125% user never sees the layout jump. Deliberately written
 * as a compact, dependency-free IIFE that can never throw.
 */
export const APPEARANCE_INIT_SCRIPT = `(function(){try{
var d=document.documentElement;
var g=function(k,f,a){try{var v=localStorage.getItem(k);return a.indexOf(v)>=0?v:f}catch(e){return f}};
var t=g('${APPEARANCE_KEYS.theme}','${APPEARANCE_DEFAULTS.theme}',${JSON.stringify(THEME_IDS)});
var dark=t==='dark'||(t==='auto'&&!!window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches);
if(dark){d.classList.add('dark')}else{d.classList.remove('dark')}
d.style.colorScheme=dark?'dark':'light';
d.setAttribute('data-bg',g('${APPEARANCE_KEYS.background}','${APPEARANCE_DEFAULTS.background}',${JSON.stringify(BACKGROUND_IDS)}));
d.setAttribute('data-accent',g('${APPEARANCE_KEYS.accent}','${APPEARANCE_DEFAULTS.accent}',${JSON.stringify(ACCENT_IDS)}));
d.setAttribute('data-scale',g('${APPEARANCE_KEYS.scale}','${APPEARANCE_DEFAULTS.scale}',${JSON.stringify(SCALE_IDS)}));
d.setAttribute('data-motion',g('${APPEARANCE_KEYS.motion}','${APPEARANCE_DEFAULTS.motion}',${JSON.stringify(MOTION_IDS)}));
}catch(e){}})();`;
