import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiClient, ApiError } from "./lib/api";
import {
  applyTelegramTheme,
  initializeTelegram,
  type TelegramContext,
} from "./lib/telegram";
import type { ClubMembership, MeResponse, ProfileInput } from "./models";
import { AppShell } from "./components/AppShell";
import { Brand } from "./components/Brand";
import { ProfileForm } from "./components/ProfileForm";

type AppState =
  | { kind: "loading" }
  | { kind: "auth-error" }
  | { kind: "error"; message: string }
  | { kind: "invite-required" }
  | { kind: "register"; inviteToken: string }
  | { kind: "choose-club"; clubs: ClubMembership[] }
  | { kind: "profile-required"; club: ClubMembership; me: MeResponse }
  | { kind: "ready"; club: ClubMembership; me: MeResponse };

const errorMessages: Record<string, string> = {
  TELEGRAM_AUTH_REQUIRED: "Откройте приложение из Telegram.",
  TELEGRAM_AUTH_EXPIRED: "Сессия Telegram устарела. Закройте и откройте приложение снова.",
  TELEGRAM_AUTH_INVALID: "Не удалось подтвердить вход через Telegram.",
  CLUB_ACCESS_DENIED: "Доступ к клубу ограничен.",
  NETWORK_ERROR: "Сервер недоступен. Проверьте соединение и повторите.",
};

export default function App() {
  const [telegram] = useState<TelegramContext>(initializeTelegram);
  const api = useMemo(() => new ApiClient(telegram.initData), [telegram.initData]);
  const [state, setState] = useState<AppState>({ kind: "loading" });

  const loadClub = useCallback(
    async (club: ClubMembership) => {
      const me = await api.me(club.id);
      if (me.registration_state === "PROFILE_REQUIRED") {
        setState({ kind: "profile-required", club, me });
      } else if (me.registration_state === "REGISTERED") {
        setState({ kind: "ready", club, me });
      } else {
        setState({ kind: "invite-required" });
      }
    },
    [api],
  );

  const bootstrap = useCallback(async () => {
    if (!telegram.initData) {
      setState({ kind: "auth-error" });
      return;
    }
    if (telegram.inviteToken) {
      setState({ kind: "register", inviteToken: telegram.inviteToken });
      return;
    }
    setState({ kind: "loading" });
    try {
      const { clubs, blocked } = await api.myClubs();
      if (blocked && clubs.length === 0) {
        setState({ kind: "error", message: "Доступ к клубу ограничен." });
        return;
      }
      if (clubs.length === 0) setState({ kind: "invite-required" });
      else if (clubs.length === 1) await loadClub(clubs[0]);
      else setState({ kind: "choose-club", clubs });
    } catch (error) {
      setState({ kind: "error", message: displayError(error) });
    }
  }, [api, loadClub, telegram.initData, telegram.inviteToken]);

  useEffect(() => {
    applyTelegramTheme(telegram.webApp);
    const updateTheme = () => applyTelegramTheme(telegram.webApp);
    telegram.webApp?.onEvent?.("themeChanged", updateTheme);
    return () => telegram.webApp?.offEvent?.("themeChanged", updateTheme);
  }, [telegram.webApp]);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  async function register(values: ProfileInput) {
    if (state.kind !== "register") return;
    const result = await api.register(state.inviteToken, values);
    const club: ClubMembership = {
      id: result.club_id,
      name: "Ваш клуб",
      membership_role: result.membership_role,
      membership_status: result.membership_status,
    };
    const { clubs } = await api.myClubs();
    const resolvedClub = clubs.find((item) => item.id === result.club_id) ?? club;
    await loadClub(resolvedClub);
  }

  async function completeProfile(values: ProfileInput) {
    if (state.kind !== "profile-required") return;
    await api.completeProfile(state.club.id, values);
    await loadClub(state.club);
  }

  return (
    <div className="root-layout">
      {state.kind !== "ready" && state.kind !== "profile-required" && (
        <header className="brand-header"><Brand /></header>
      )}
      {state.kind === "loading" && <StatusCard title="Входим в клуб…" busy />}
      {state.kind === "auth-error" && (
        <StatusCard
          title="Нужен Telegram"
          text="Откройте приложение кнопкой в боте или по ссылке-приглашению."
        />
      )}
      {state.kind === "error" && (
        <StatusCard title="Не удалось войти" text={state.message} action="Повторить" onAction={bootstrap} />
      )}
      {state.kind === "invite-required" && (
        <StatusCard
          title="Необходимо приглашение"
          text="Откройте персональную ссылку клуба в Telegram."
          action="Проверить снова"
          onAction={bootstrap}
        />
      )}
      {state.kind === "register" && (
        <ProfileForm
          title="Регистрация"
          subtitle="Представьтесь клубу — это займёт меньше минуты."
          submitLabel="Зарегистрироваться"
          onSubmit={register}
        />
      )}
      {state.kind === "choose-club" && (
        <ClubChooser clubs={state.clubs} onChoose={loadClub} />
      )}
      {state.kind === "profile-required" && (
        <ProfileForm
          title="Завершите профиль"
          subtitle={`Для входа в «${state.club.name}» заполните обязательные поля.`}
          submitLabel="Сохранить и войти"
          initial={{
            first_name: state.me.profile?.first_name ?? "",
            last_name: state.me.profile?.last_name ?? "",
            school_nickname: state.me.profile?.school_nickname ?? "",
          }}
          onSubmit={completeProfile}
        />
      )}
      {state.kind === "ready" && <AppShell club={state.club} me={state.me} />}
    </div>
  );
}

function ClubChooser({ clubs, onChoose }: { clubs: ClubMembership[]; onChoose: (club: ClubMembership) => Promise<void> }) {
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function choose(club: ClubMembership) {
    setLoading(club.id);
    setError(null);
    try { await onChoose(club); }
    catch (reason) { setError(displayError(reason)); setLoading(null); }
  }
  return (
    <main className="center-shell">
      <section className="status-card">
        <p className="eyebrow">Ваши клубы</p><h1>Выберите клуб</h1>
        {error && <div className="alert alert--error">{error}</div>}
        <div className="club-list">
          {clubs.map((club) => (
            <button key={club.id} disabled={loading !== null} onClick={() => void choose(club)}>
              <span><strong>{club.name}</strong><small>{club.membership_status === "INACTIVE" ? "Только просмотр" : "Активен"}</small></span>
              <span aria-hidden="true">→</span>
            </button>
          ))}
        </div>
      </section>
    </main>
  );
}

function StatusCard({ title, text, busy, action, onAction }: { title: string; text?: string; busy?: boolean; action?: string; onAction?: () => void | Promise<void> }) {
  return (
    <main className="center-shell"><section className="status-card">
      {busy && <div className="spinner" aria-label="Загрузка" />}
      <h1>{title}</h1>{text && <p>{text}</p>}
      {action && <button className="primary-button" onClick={() => void onAction?.()}>{action}</button>}
    </section></main>
  );
}

function displayError(error: unknown): string {
  if (error instanceof ApiError) return errorMessages[error.code] ?? "Не удалось выполнить запрос.";
  return errorMessages.NETWORK_ERROR;
}
