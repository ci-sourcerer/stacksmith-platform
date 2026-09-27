import { Blocks, Github, ListChecks, Moon, Settings2, Sun } from "lucide-react";
import type { Session } from "../types";
import { useDarkMode } from "../useDarkMode";

export function PlatformHeader({
  session,
  active,
  onSignOut,
}: {
  session: Session | null | undefined;
  active: "composer" | "runs" | "settings";
  onSignOut: () => void;
}) {
  const [dark, toggleDark] = useDarkMode();
  return (
    <header className="topbar">
      <a className="brand" href="/" aria-label="Stacksmith Platform home">
        <svg className="brand-mark" viewBox="0 0 64 64" aria-hidden="true">
          <rect x="4" y="4" width="56" height="56" rx="16" fill="#315f4b" />
          <path
            d="M19 21h-4v22h4M45 21h4v22h-4"
            fill="none"
            stroke="#dff0c4"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="4"
          />
          <path
            d="m22 24 10-5 10 5-10 5-10-5Zm0 8 10 5 10-5M22 39l10 5 10-5"
            fill="none"
            stroke="#f5f7ee"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="3.5"
          />
        </svg>
        <strong>
          stacksmith<span>platform</span>
        </strong>
      </a>
      {session && (session.mode === "demo" || session.login) && (
        <nav className="product-nav" aria-label="Platform">
          <a href="/" aria-current={active === "composer" ? "page" : undefined}>
            <Blocks size={16} /> Composer
          </a>
          {session.execution_enabled && (
            <a
              href="/runs"
              aria-current={active === "runs" ? "page" : undefined}
            >
              <ListChecks size={16} /> Runs
            </a>
          )}
          <a
            href="/settings"
            aria-current={active === "settings" ? "page" : undefined}
          >
            <Settings2 size={16} /> Settings
          </a>
        </nav>
      )}
      <div className="topbar-right">
        {session?.mode === "demo" ? (
          <span className="badge demo-badge">LOCAL EXAMPLE</span>
        ) : session?.login ? (
          <>
            <span className="user-badge">
              <Github size={15} />
              {session.login}
            </span>
            <button className="text-button" onClick={onSignOut}>
              Sign out
            </button>
          </>
        ) : null}
        <span className="version-badge">EARLY PREVIEW</span>
        <button
          className="theme-toggle"
          onClick={toggleDark}
          aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
          title={dark ? "Switch to light mode" : "Switch to dark mode"}
        >
          {dark ? <Sun size={16} /> : <Moon size={16} />}
        </button>
      </div>
    </header>
  );
}
