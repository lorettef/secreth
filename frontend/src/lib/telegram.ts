export interface TelegramThemeParams {
  bg_color?: string;
  text_color?: string;
  hint_color?: string;
  link_color?: string;
  button_color?: string;
  button_text_color?: string;
  secondary_bg_color?: string;
  section_bg_color?: string;
  section_separator_color?: string;
}

export interface TelegramWebApp {
  initData: string;
  initDataUnsafe?: { start_param?: string };
  colorScheme?: "light" | "dark";
  themeParams?: TelegramThemeParams;
  ready(): void;
  expand(): void;
  onEvent?(event: "themeChanged", callback: () => void): void;
  offEvent?(event: "themeChanged", callback: () => void): void;
  setHeaderColor?(color: string): void;
  setBackgroundColor?(color: string): void;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

export interface TelegramContext {
  webApp: TelegramWebApp | null;
  initData: string;
  inviteToken: string | null;
}

export function initializeTelegram(): TelegramContext {
  const webApp = window.Telegram?.WebApp ?? null;
  webApp?.ready();
  webApp?.expand();
  const inviteToken =
    webApp?.initDataUnsafe?.start_param ??
    new URLSearchParams(window.location.search).get("tgWebAppStartParam");
  return {
    webApp,
    initData: webApp?.initData ?? "",
    inviteToken: inviteToken || null,
  };
}

export function applyTelegramTheme(webApp: TelegramWebApp | null): void {
  const root = document.documentElement;
  const scheme = webApp?.colorScheme ?? "light";
  root.dataset.theme = scheme;
  const params = webApp?.themeParams ?? {};
  const variables: Record<keyof TelegramThemeParams, string> = {
    bg_color: "--tg-theme-bg-color",
    text_color: "--tg-theme-text-color",
    hint_color: "--tg-theme-hint-color",
    link_color: "--tg-theme-link-color",
    button_color: "--tg-theme-button-color",
    button_text_color: "--tg-theme-button-text-color",
    secondary_bg_color: "--tg-theme-secondary-bg-color",
    section_bg_color: "--tg-theme-section-bg-color",
    section_separator_color: "--tg-theme-section-separator-color",
  };
  for (const [key, variable] of Object.entries(variables)) {
    const value = params[key as keyof TelegramThemeParams];
    if (value) root.style.setProperty(variable, value);
  }
  webApp?.setHeaderColor?.("#151D29");
  webApp?.setBackgroundColor?.(params.bg_color ?? (scheme === "dark" ? "#151D29" : "#F4F6F8"));
}
