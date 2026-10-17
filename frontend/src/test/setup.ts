import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

beforeEach(() => {
  window.location.hash = "";
  window.history.replaceState({}, "", "/");
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("style");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  delete window.Telegram;
});
