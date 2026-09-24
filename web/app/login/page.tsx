"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const AUTH_MODE = process.env.NEXT_PUBLIC_AUTH_MODE ?? "dev";

/**
 * Two modes, chosen at build time (Task 13/14, same env-driven convention
 * as NEXT_PUBLIC_API_BASE_URL -- no .env file exists in this repo, a real
 * deploy sets these at `pnpm build` time):
 *
 * - `NEXT_PUBLIC_AUTH_MODE=cognito` (production): a plain navigation to
 *   `GET /api/v1/auth/login`, which redirects to Cognito's real hosted UI
 *   (api/routers/auth.py, live and verified -- Task 18.2). This must be a
 *   real top-level navigation, not `fetch`, since the browser needs to
 *   follow the redirect to Cognito and back.
 * - anything else (default, local dev): today's `/auth/dev-login` form,
 *   an explicit local-only stand-in (see auth.py's module docstring) --
 *   `run_local_api.py` never sets `include_dev_login=False`, so this
 *   keeps working locally with zero backend change.
 */
export default function LoginPage() {
  if (AUTH_MODE === "cognito") {
    return <CognitoLoginPanel />;
  }
  return <DevLoginPanel />;
}

function CognitoLoginPanel() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-md rounded-2xl border border-line bg-surface p-8 shadow-card">
        <svg aria-hidden width="44" height="44" viewBox="0 0 28 28">
          <rect width="28" height="28" rx="7" fill="var(--brand)" />
          <path d="M6 19c3-7 6-9 9-5s5 2 7-4" stroke="var(--on-brand)" strokeWidth="2.4" fill="none" strokeLinecap="round" />
          <circle cx="22" cy="10" r="2" fill="var(--gold)" />
        </svg>
        <p className="mt-6 text-xs font-semibold uppercase tracking-wider text-brand">NVJCYO Cross Country</p>
        <h1 className="mt-1 text-3xl font-bold tracking-tight">XC Data</h1>
        <p className="mt-2 text-sm text-muted">
          Results, standings, and athlete progress across every developmental meet since 2023.
        </p>
        <a
          href={`${API_BASE}/api/v1/auth/login`}
          className="mt-8 block rounded-lg bg-brand px-4 py-2.5 text-center font-semibold text-on-brand hover:bg-brand-strong"
        >
          Sign in
        </a>
        <p className="mt-4 text-center text-xs text-muted">Access is by invitation.</p>
      </div>
    </main>
  );
}

function DevLoginPanel() {
  const router = useRouter();
  const [subject, setSubject] = useState("coach@example.com");
  const [role, setRole] = useState<"admin" | "viewer">("viewer");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const url = new URL(`${API_BASE}/api/v1/auth/dev-login`);
      url.searchParams.set("subject", subject);
      url.searchParams.set("role", role);
      const response = await fetch(url.toString(), { method: "POST", credentials: "include" });
      if (!response.ok) {
        throw new Error(`sign-in failed (${response.status})`);
      }
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "sign-in failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-6 px-4">
      <div>
        <h1 className="text-2xl font-semibold">XC Data Platform</h1>
        <p className="mt-1 text-sm text-muted">
          Invite-only sign-in (design.md &sect;13.1).
        </p>
      </div>

      <div
        role="note"
        className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
      >
        Local development stand-in only &mdash; production sign-in redirects to Cognito&apos;s
        hosted UI, not this form.
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1 text-sm">
          <span>Identity (Cognito subject)</span>
          <input
            className="rounded-lg border border-line bg-surface px-3 py-2"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
            required
          />
        </label>
        <fieldset className="flex flex-col gap-1 text-sm">
          <legend>Role</legend>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="role"
              checked={role === "viewer"}
              onChange={() => setRole("viewer")}
            />
            Viewer
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="role"
              checked={role === "admin"}
              onChange={() => setRole("admin")}
            />
            Admin
          </label>
        </fieldset>

        {error && (
          <p role="alert" className="text-sm text-red-700 dark:text-red-400">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="rounded-lg bg-brand px-4 py-2 font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-60"
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
