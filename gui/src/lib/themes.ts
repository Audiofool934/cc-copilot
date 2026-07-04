// Curated GUI themes, mirrored from the TUI's COCKPIT_THEME_SPECS.
// Each theme maps semantic tokens to the CSS variables used by the components.

export interface ThemeSpec {
  name: string;
  label: string;
  description: string;
  bg: string;
  panel: string;
  panel2: string;
  text: string;
  muted: string;
  accent: string;
  good: string;
  warn: string;
  bad: string;
  border: string;
}

export const THEMES: ThemeSpec[] = [
  {
    name: "cockpit",
    label: "Cockpit",
    description: "graphite, apricot, blue, and the Claude×Codex blend",
    bg: "#1e1e1e",
    panel: "#1e1e1e",
    panel2: "#262626",
    text: "#c0caf5",
    muted: "#6c7086",
    accent: "#807ea6",
    good: "#9ece6a",
    warn: "#e0af68",
    bad: "#f7768e",
    border: "#353535",
  },
  {
    name: "graphite",
    label: "Graphite",
    description: "charcoal, steel, cyan, and amber",
    bg: "#111318",
    panel: "#20252d",
    panel2: "#191d24",
    text: "#d7dee8",
    muted: "#7a8494",
    accent: "#f5a97f",
    good: "#a6da95",
    warn: "#eed49f",
    bad: "#ed8796",
    border: "#272d36",
  },
  {
    name: "signal",
    label: "Signal",
    description: "near-black, green, blue, and coral",
    bg: "#0f1412",
    panel: "#1d2521",
    panel2: "#151c19",
    text: "#d8e2dc",
    muted: "#738078",
    accent: "#ffb86c",
    good: "#9ece6a",
    warn: "#e0af68",
    bad: "#ff7b72",
    border: "#24302a",
  },
  {
    name: "daybreak",
    label: "Daybreak",
    description: "light, quiet, blue, and persimmon",
    bg: "#f5f7fa",
    panel: "#e4eaf0",
    panel2: "#eef2f6",
    text: "#1f2933",
    muted: "#697586",
    accent: "#b85c38",
    good: "#3f7d4f",
    warn: "#9a6b16",
    bad: "#b23a48",
    border: "#dae3ec",
  },
];

export const THEME_NAMES = THEMES.map((t) => t.name);

export function themeByName(name: string): ThemeSpec | undefined {
  return THEMES.find((t) => t.name === name);
}

export function applyTheme(name: string): void {
  const t = themeByName(name);
  if (!t || typeof document === "undefined") return;
  const root = document.documentElement;
  root.style.setProperty("--bg", t.bg);
  root.style.setProperty("--panel", t.panel);
  root.style.setProperty("--panel-2", t.panel2);
  root.style.setProperty("--text", t.text);
  root.style.setProperty("--muted", t.muted);
  root.style.setProperty("--accent", t.accent);
  root.style.setProperty("--good", t.good);
  root.style.setProperty("--warn", t.warn);
  root.style.setProperty("--bad", t.bad);
  root.style.setProperty("--border", t.border);
}
