"use client";

// Authenticated app frame (Task 19.0): brand, primary navigation, global
// athlete/school search, and the session guard. Every signed-in page
// renders inside this, so navigation and auth behavior are identical
// everywhere. Children render only once the session is confirmed.

import { createContext, useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, type AthleteSummary, type SchoolSummary, type SessionInfo } from "@/lib/api";
import { useSession } from "@/lib/useSession";
import { athleteHref, cx, schoolHref } from "./ui";

const SessionContext = createContext<SessionInfo | null>(null);

export function useCurrentSession(): SessionInfo {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useCurrentSession must be used inside <AppShell>");
  return session;
}

const NAV = [
  { href: "/dashboard", label: "Overview" },
  { href: "/athletes", label: "Athletes" },
  { href: "/schools", label: "Schools" },
  { href: "/standings", label: "Standings" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const session = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

  if (session.status !== "authenticated") {
    return (
      <div className="flex min-h-screen items-center justify-center" aria-busy="true">
        <p role="status" className="text-sm text-muted">
          {session.status === "loading" ? "Checking your session…" : "Redirecting to sign in…"}
        </p>
      </div>
    );
  }

  const links = [
    ...NAV,
    ...(session.session.agent_access ? [{ href: "/ask", label: "Ask" }] : []),
    ...(session.session.role === "admin" ? [{ href: "/admin", label: "Admin" }] : []),
  ];
  const isActive = (href: string) => pathname === href || pathname?.startsWith(`${href}/`);

  return (
    <SessionContext.Provider value={session.session}>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-30 border-b border-line bg-surface/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-3">
          <a href="/dashboard" className="flex shrink-0 items-center gap-2 font-bold tracking-tight">
            <Logo />
            <span className="hidden sm:inline">XC Data</span>
          </a>
          <nav aria-label="Primary" className="hidden items-center gap-1 md:flex">
            {links.map((link) => (
              <a
                key={link.href}
                href={link.href}
                aria-current={isActive(link.href) ? "page" : undefined}
                className={cx(
                  "rounded-md px-3 py-1.5 text-sm font-medium",
                  isActive(link.href)
                    ? "bg-brand-soft text-brand"
                    : "text-muted hover:bg-surface-2 hover:text-fg",
                )}
              >
                {link.label}
              </a>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <GlobalSearch />
            <button
              type="button"
              onClick={() => api.logout().finally(() => router.push("/login"))}
              className="hidden rounded-md px-3 py-1.5 text-sm text-muted hover:bg-surface-2 hover:text-fg sm:block"
            >
              Sign out
            </button>
            <button
              type="button"
              className="rounded-md p-2 text-muted hover:bg-surface-2 md:hidden"
              aria-expanded={menuOpen}
              aria-controls="mobile-nav"
              onClick={() => setMenuOpen((open) => !open)}
            >
              <span className="sr-only">Menu</span>
              <svg aria-hidden width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
                <path d="M3 5h14v2H3zm0 4h14v2H3zm0 4h14v2H3z" />
              </svg>
            </button>
          </div>
        </div>
        {menuOpen && (
          <nav id="mobile-nav" aria-label="Primary" className="border-t border-line px-4 py-2 md:hidden">
            {links.map((link) => (
              <a
                key={link.href}
                href={link.href}
                aria-current={isActive(link.href) ? "page" : undefined}
                className="block rounded-md px-3 py-2 text-sm font-medium hover:bg-surface-2"
              >
                {link.label}
              </a>
            ))}
            <button
              type="button"
              onClick={() => api.logout().finally(() => router.push("/login"))}
              className="block w-full rounded-md px-3 py-2 text-left text-sm text-muted hover:bg-surface-2"
            >
              Sign out
            </button>
          </nav>
        )}
      </header>
      <main id="main" className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-6 sm:py-8">
        {children}
      </main>
      <footer className="mx-auto max-w-6xl px-4 pb-8 text-xs text-muted">
        NVJCYO Cross Country · signed in as {session.session.role}
      </footer>
    </SessionContext.Provider>
  );
}

function Logo() {
  return (
    <svg aria-hidden width="28" height="28" viewBox="0 0 28 28">
      <rect width="28" height="28" rx="7" fill="var(--brand)" />
      <path
        d="M6 19c3-7 6-9 9-5s5 2 7-4"
        stroke="var(--on-brand)"
        strokeWidth="2.4"
        fill="none"
        strokeLinecap="round"
      />
      <circle cx="22" cy="10" r="2" fill="var(--gold)" />
    </svg>
  );
}

type SearchHit =
  | { kind: "athlete"; id: string; name: string }
  | { kind: "school"; id: string; name: string };

function GlobalSearch() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const listId = useId();
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setHits([]);
      return;
    }
    const handle = setTimeout(() => {
      Promise.all([api.athletes(trimmed, 8), api.schools(trimmed, 4)])
        .then(([athletes, schools]) => {
          setHits([
            ...schools.data.map((s: SchoolSummary) => ({
              kind: "school" as const,
              id: s.school_id,
              name: s.display_name,
            })),
            ...athletes.data.map((a: AthleteSummary) => ({
              kind: "athlete" as const,
              id: a.athlete_id,
              name: a.display_name,
            })),
          ]);
          setActiveIndex(-1);
          setOpen(true);
        })
        .catch(() => setHits([]));
    }, 180);
    return () => clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const go = (hit: SearchHit) => {
    window.location.href = hit.kind === "athlete" ? athleteHref(hit.id) : schoolHref(hit.id);
  };

  return (
    <div ref={containerRef} className="relative">
      <label className="sr-only" htmlFor={`${listId}-input`}>
        Search athletes and schools
      </label>
      <input
        id={`${listId}-input`}
        type="search"
        role="combobox"
        aria-expanded={open && hits.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
        placeholder="Search athletes & schools"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onFocus={() => hits.length > 0 && setOpen(true)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setActiveIndex((i) => Math.min(i + 1, hits.length - 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setActiveIndex((i) => Math.max(i - 1, 0));
          } else if (event.key === "Enter") {
            const hit = hits[Math.max(activeIndex, 0)];
            if (hit) go(hit);
          } else if (event.key === "Escape") {
            setOpen(false);
          }
        }}
        className="w-40 rounded-lg border border-line bg-surface-2 px-3 py-1.5 text-sm placeholder:text-muted focus:w-64 sm:w-56 md:focus:w-72"
      />
      {open && query.trim().length >= 2 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute right-0 z-40 mt-1 max-h-96 w-72 overflow-auto rounded-lg border border-line bg-surface p-1 shadow-card"
        >
          {hits.length === 0 && <li className="px-3 py-2 text-sm text-muted">No matches</li>}
          {hits.map((hit, index) => (
            <li
              key={`${hit.kind}-${hit.id}`}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === activeIndex}
              onMouseDown={(event) => {
                event.preventDefault();
                go(hit);
              }}
              className={cx(
                "flex cursor-pointer items-center justify-between gap-2 rounded-md px-3 py-2 text-sm",
                index === activeIndex ? "bg-brand-soft" : "hover:bg-surface-2",
              )}
            >
              <span className="truncate">{hit.name}</span>
              <span className="shrink-0 text-xs text-muted">
                {hit.kind === "athlete" ? "Athlete" : "School"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
