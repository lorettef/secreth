import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import App from "./App";
import type { ClubMembership, MeResponse } from "./models";

const activeClub: ClubMembership = {
  id: "club-1",
  name: "Клуб Якутска",
  membership_role: "PLAYER",
  membership_status: "ACTIVE",
};

function me(overrides: Partial<MeResponse> = {}): MeResponse {
  return {
    authenticated: true,
    telegram_id: 42,
    registration_state: "REGISTERED",
    club_id: "club-1",
    membership_id: "membership-1",
    membership_role: "PLAYER",
    membership_status: "ACTIVE",
    profile: {
      id: "player-1",
      display_name: "Иван Петров",
      first_name: "Иван",
      last_name: "Петров",
      school_nickname: "ivan",
    },
    ...overrides,
  };
}

function telegram(startParam?: string, colorScheme: "light" | "dark" = "light") {
  const ready = vi.fn();
  const expand = vi.fn();
  window.Telegram = {
    WebApp: {
      initData: "signed-init-data",
      initDataUnsafe: startParam ? { start_param: startParam } : {},
      colorScheme,
      themeParams: colorScheme === "dark" ? { bg_color: "#101722" } : {},
      ready,
      expand,
    },
  };
  return { ready, expand };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(...responses: Array<Response | (() => Response | Promise<Response>)>) {
  const queue = [...responses];
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
    const next = queue.shift();
    if (!next) throw new Error("Unexpected request");
    return typeof next === "function" ? next() : next;
  });
}

async function fillRegistration() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Имя"), "Иван");
  await user.type(screen.getByLabelText("Фамилия"), "Петров");
  await user.type(screen.getByLabelText("Ник Школы 21"), "ivan");
  return user;
}

test("registers from start_param and opens the real club without a welcome screen", async () => {
  const tg = telegram("one-time-secret");
  const fetchMock = mockFetch(
    json({
      registration_state: "REGISTERED",
      club_id: activeClub.id,
      membership_id: "membership-1",
      membership_role: "PLAYER",
      membership_status: "ACTIVE",
      profile: me().profile,
    }, 201),
    json({ clubs: [activeClub] }),
    json(me()),
  );
  render(<App />);
  const user = await fillRegistration();
  await user.click(screen.getByRole("button", { name: "Зарегистрироваться" }));

  expect(await screen.findByRole("heading", { name: activeClub.name })).toBeInTheDocument();
  expect(screen.getByText("@ivan")).toBeInTheDocument();
  expect(tg.ready).toHaveBeenCalled();
  const [url, request] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/v1/onboarding/register");
  expect(request?.headers).toMatchObject({ Authorization: "tma signed-init-data" });
  expect(request?.body).toContain('"invite_token":"one-time-secret"');
  expect(window.localStorage.length).toBe(0);
});

test("keeps form values and displays field errors", async () => {
  telegram("invite");
  render(<App />);
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Имя"), "Иван");
  await user.click(screen.getByRole("button", { name: "Зарегистрироваться" }));
  expect(screen.getByText("Введите фамилию.")).toBeInTheDocument();
  expect(screen.getByText("Введите ник Школы 21.")).toBeInTheDocument();
  expect(screen.getByLabelText("Имя")).toHaveValue("Иван");
});

test.each([
  ["SCHOOL_NICKNAME_CONFLICT", "Этот ник уже занят в клубе."],
  ["INVITE_INVALID", "Приглашение недействительно или уже использовано."],
])("shows safe onboarding error %s", async (code, message) => {
  telegram("invite");
  mockFetch(json({ detail: code }, code === "INVITE_INVALID" ? 400 : 409));
  render(<App />);
  const user = await fillRegistration();
  await user.click(screen.getByRole("button", { name: "Зарегистрироваться" }));
  expect(await screen.findByText(message)).toBeInTheDocument();
  expect(screen.getByLabelText("Имя")).toHaveValue("Иван");
});

test("prevents a double registration submit while the request is pending", async () => {
  telegram("invite");
  let resolve!: (response: Response) => void;
  const pending = new Promise<Response>((done) => { resolve = done; });
  const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValue(pending);
  render(<App />);
  const user = await fillRegistration();
  const button = screen.getByRole("button", { name: "Зарегистрироваться" });
  await user.click(button);
  fireEvent.click(button);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(button).toBeDisabled();
  resolve(json({ detail: "INVITE_INVALID" }, 400));
  expect(await screen.findByText("Приглашение недействительно или уже использовано.")).toBeInTheDocument();
});

test("reopens without invite and automatically selects the only club", async () => {
  telegram();
  mockFetch(json({ clubs: [activeClub] }), json(me()));
  render(<App />);
  expect(await screen.findByRole("heading", { name: activeClub.name })).toBeInTheDocument();
});

test("offers a club chooser for multiple memberships including inactive", async () => {
  telegram();
  const inactive: ClubMembership = { ...activeClub, id: "club-2", name: "Архивный клуб", membership_status: "INACTIVE" };
  mockFetch(json({ clubs: [activeClub, inactive] }), json(me({ club_id: inactive.id, membership_status: "INACTIVE" })));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Выберите клуб" })).toBeInTheDocument();
  expect(screen.getByText("Только просмотр")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /Архивный клуб/ }));
  expect(await screen.findByText("Иван Петров")).toBeInTheDocument();
  expect(screen.getByText("Только просмотр")).toBeInTheDocument();
});

test("shows invite required when there are no accessible memberships", async () => {
  telegram();
  mockFetch(json({ clubs: [] }));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Необходимо приглашение" })).toBeInTheDocument();
});

test("shows a neutral blocked response without club data", async () => {
  telegram();
  mockFetch(json({ clubs: [], blocked: true }));
  render(<App />);
  expect(await screen.findByText("Доступ к клубу ограничен.")).toBeInTheDocument();
  expect(screen.queryByText(activeClub.name)).not.toBeInTheDocument();
});

test("completes PROFILE_REQUIRED without an invitation", async () => {
  telegram();
  const required = me({ registration_state: "PROFILE_REQUIRED", profile: null });
  mockFetch(
    json({ clubs: [activeClub] }),
    json(required),
    json({ ...me(), registration_state: "REGISTERED" }),
    json(me()),
  );
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Завершите профиль" })).toBeInTheDocument();
  const user = await fillRegistration();
  await user.click(screen.getByRole("button", { name: "Сохранить и войти" }));
  expect(await screen.findByRole("heading", { name: activeClub.name })).toBeInTheDocument();
});

test("allows retry after a network failure", async () => {
  telegram();
  const fetchMock = vi.spyOn(globalThis, "fetch")
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(json({ clubs: [activeClub] }))
    .mockResolvedValueOnce(json(me()));
  render(<App />);
  expect(await screen.findByText(/Сервер недоступен/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
  expect(await screen.findByRole("heading", { name: activeClub.name })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

test("handles missing Telegram initData and applies dark theme", async () => {
  telegram(undefined, "dark");
  window.Telegram!.WebApp!.initData = "";
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Нужен Telegram" })).toBeInTheDocument();
  expect(document.documentElement).toHaveAttribute("data-theme", "dark");
});

test("renders all five routes without invented game data", async () => {
  telegram();
  mockFetch(json({ clubs: [activeClub] }), json(me()));
  render(<App />);
  await screen.findByRole("heading", { name: activeClub.name });
  await userEvent.click(screen.getByRole("button", { name: /Рейтинг/ }));
  expect(screen.getByText(/Раздел ещё не подключён/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /Профиль/ }));
  expect(screen.getByText("Ник Школы 21")).toBeInTheDocument();
  expect(screen.getByText("ivan")).toBeInTheDocument();
});
