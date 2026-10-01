import { useColorScheme } from "react-native";

const light = {
  bg: "#f5f7f6",
  surface: "#ffffff",
  surface2: "#eef2f0",
  text: "#17211e",
  muted: "#5b6a65",
  border: "#d9e0dd",
  primary: "#0f5c4d",
  onPrimary: "#ffffff",
  good: "#146c43",
  goodSoft: "#e3f4ea",
  warn: "#8a5a00",
  warnSoft: "#fff4d6",
  danger: "#b42318",
  dangerSoft: "#fdecea",
};
const dark: typeof light = {
  bg: "#0f1513",
  surface: "#161e1b",
  surface2: "#1d2724",
  text: "#e6ece9",
  muted: "#9db0a9",
  border: "#2c3a35",
  primary: "#3fb89a",
  onPrimary: "#06221b",
  good: "#6fd39b",
  goodSoft: "#163325",
  warn: "#f0c46a",
  warnSoft: "#3a2f14",
  danger: "#f2877e",
  dangerSoft: "#3a1c1a",
};

export type Theme = typeof light;

export function useTheme(): Theme {
  return useColorScheme() === "dark" ? dark : light;
}

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 };
export const radius = { sm: 8, md: 12, lg: 16 };
