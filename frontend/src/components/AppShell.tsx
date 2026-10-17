import { useEffect, useState } from "react";
import type { ClubMembership, MeResponse } from "../models";
import { Brand } from "./Brand";

type Route = "home" | "evening" | "rating" | "history" | "profile";

const tabs: { route: Route; label: string; icon: string }[] = [
  { route: "home", label: "Главная", icon: "⌂" },
  { route: "evening", label: "Вечер", icon: "◉" },
  { route: "rating", label: "Рейтинг", icon: "◇" },
  { route: "history", label: "История", icon: "≡" },
  { route: "profile", label: "Профиль", icon: "○" },
];

function currentRoute(): Route {
  const candidate = window.location.hash.replace("#/", "") as Route;
  return tabs.some(({ route }) => route === candidate) ? candidate : "home";
}

export function AppShell({ club, me }: { club: ClubMembership; me: MeResponse }) {
  const [route, setRoute] = useState<Route>(currentRoute);
  useEffect(() => {
    const onHash = () => setRoute(currentRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function navigate(next: Route) {
    window.location.hash = `/${next}`;
    setRoute(next);
  }

  const profile = me.profile!;
  return (
    <div className="app-shell">
      <header className="topbar">
        <Brand compact />
        {me.membership_status === "INACTIVE" && (
          <span className="status-pill">Только просмотр</span>
        )}
      </header>
      <main className="app-content">
        {route === "home" && (
          <section>
            <p className="eyebrow">Ваш клуб</p>
            <h1>{club.name}</h1>
            <article className="profile-card">
              <Avatar name={profile.display_name} />
              <div>
                <h2>{profile.display_name}</h2>
                <p>@{profile.school_nickname}</p>
                <span>{roleLabel(me.membership_role)}</span>
              </div>
            </article>
            <article className="info-card">
              <h2>Клуб открыт</h2>
              <p>Игровые данные появятся здесь в следующих этапах.</p>
            </article>
          </section>
        )}
        {route === "profile" && (
          <section>
            <p className="eyebrow">Личный профиль</p>
            <h1>{profile.display_name}</h1>
            <dl className="details-card">
              <div><dt>Имя</dt><dd>{profile.first_name}</dd></div>
              <div><dt>Фамилия</dt><dd>{profile.last_name}</dd></div>
              <div><dt>Ник Школы 21</dt><dd>{profile.school_nickname}</dd></div>
              <div><dt>Статус</dt><dd>{me.membership_status === "ACTIVE" ? "Активен" : "Неактивен"}</dd></div>
            </dl>
          </section>
        )}
        {route !== "home" && route !== "profile" && (
          <section className="pending-section">
            <span className="pending-section__mark">{tabs.find((tab) => tab.route === route)?.icon}</span>
            <h1>{tabs.find((tab) => tab.route === route)?.label}</h1>
            <p>Раздел ещё не подключён к серверу и будет реализован на следующем этапе.</p>
          </section>
        )}
      </main>
      <nav className="bottom-nav" aria-label="Основные разделы">
        {tabs.map((tab) => (
          <button
            key={tab.route}
            className={route === tab.route ? "is-active" : ""}
            aria-current={route === tab.route ? "page" : undefined}
            onClick={() => navigate(tab.route)}
          >
            <span aria-hidden="true">{tab.icon}</span>
            {tab.label}
          </button>
        ))}
      </nav>
    </div>
  );
}

function Avatar({ name }: { name: string }) {
  const initials = name.split(/\s+/).slice(0, 2).map((part) => part[0]).join("");
  return <div className="avatar" aria-hidden="true">{initials}</div>;
}

function roleLabel(role: MeResponse["membership_role"]): string {
  return { OWNER: "Владелец", ADMIN: "Администратор", PLAYER: "Участник" }[role ?? "PLAYER"];
}
