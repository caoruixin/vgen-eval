import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, IS_PUBLIC } from "./api";
import { useI18n } from "./i18n";
import { cls, usd } from "./util";
import ScenariosPage from "./pages/Scenarios";
import ComparePage from "./pages/Compare";
import RunsPage from "./pages/Runs";
import RunBoardPage from "./pages/RunBoard";
import LeaderboardPage from "./pages/Leaderboard";
import ModelsPage from "./pages/Models";
import SettingsPage from "./pages/Settings";

type Theme = "system" | "light" | "dark";

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("vgen.theme") as Theme) || "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("vgen.theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return [theme, setTheme];
}

function SpendPill() {
  const { t } = useI18n();
  const { data } = useQuery({ queryKey: ["config"], queryFn: api.config, refetchInterval: 10_000 });
  if (!data) return null;
  const frac = Math.min(1, data.spent_usd / Math.max(0.01, data.budget_usd));
  const color = frac > 0.9 ? "var(--critical)" : frac > 0.7 ? "var(--warn)" : "var(--good)";
  return (
    <NavLink to="/settings" className="flex items-center gap-2 text-xs text-ink-2 hover:text-ink" title={t("spend.title")}>
      <span className="tnum font-medium text-ink">{usd(data.spent_usd)}</span>
      <span className="text-muted">/ {usd(data.budget_usd, 0)}</span>
      <span className="relative h-1.5 w-16 overflow-hidden rounded-full bg-surface-3">
        <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${frac * 100}%`, background: color }} />
      </span>
    </NavLink>
  );
}

export default function App() {
  const { t, lang, setLang } = useI18n();
  const [theme, setTheme] = useTheme();
  const mock = window.__VGEN__?.mock;
  const nav: [string, string][] = [
    ["/", t("nav.scenarios")],
    ["/compare", t("nav.compare")],
    ["/runs", t("nav.runs")],
    ["/leaderboard", t("nav.leaderboard")],
    ["/models", t("nav.models")],
  ];
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-line bg-page/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center gap-6 px-4 py-2.5 sm:px-6">
          <NavLink to="/" className="flex items-center gap-2">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-ink text-surface">
              <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="currentColor" aria-hidden>
                <path d="M5 3.5v9l7.5-4.5z" />
              </svg>
            </span>
            <span className="text-[15px] font-semibold tracking-tight">vgen·eval</span>
            <span className="hidden text-xs text-muted lg:inline">{t("app.tagline")}</span>
          </NavLink>
          <nav className="flex items-center gap-1 overflow-x-auto">
            {nav.map(([to, label]) => (
              <NavLink
                key={to}
                to={to}
                end={to === "/"}
                className={({ isActive }) =>
                  cls("rounded-md px-2.5 py-1.5 text-[13px] whitespace-nowrap", isActive ? "bg-surface-3 font-medium text-ink" : "text-ink-2 hover:text-ink")
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            {mock && <span className="chip" style={{ borderColor: "var(--warn)", color: "var(--ink)" }}>⚠ {t("mock.badge")}</span>}
            {IS_PUBLIC ? (
              <span className="chip hidden sm:inline-flex" title={t("public.badge_title")}>
                {t("public.badge")}
              </span>
            ) : (
              <SpendPill />
            )}
            <div className="seg" role="group" aria-label="language">
              <button aria-pressed={lang === "en"} onClick={() => setLang("en")}>EN</button>
              <button aria-pressed={lang === "zh"} onClick={() => setLang("zh")}>中文</button>
            </div>
            <select className="input !w-auto !py-1 text-xs" value={theme} onChange={(e) => setTheme(e.target.value as Theme)} aria-label="theme">
              <option value="system">{t("theme.system")}</option>
              <option value="light">{t("theme.light")}</option>
              <option value="dark">{t("theme.dark")}</option>
            </select>
            {!IS_PUBLIC && (
              <NavLink to="/settings" className={({ isActive }) => cls("text-[13px]", isActive ? "text-ink" : "text-ink-2 hover:text-ink")}>
                {t("nav.settings")}
              </NavLink>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6">
        <Routes>
          <Route path="/" element={<ScenariosPage />} />
          <Route path="/scenarios/:packId" element={<ScenariosPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId" element={<RunBoardPage />} />
          <Route path="/leaderboard" element={<LeaderboardPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/settings" element={IS_PUBLIC ? <Navigate to="/" replace /> : <SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
