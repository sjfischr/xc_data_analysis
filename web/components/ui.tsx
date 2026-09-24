// Shared UI primitives (Task 19.0 design system). Tailwind utilities over
// the tokens in app/globals.css -- no component library.

import type { ReactNode } from "react";

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {eyebrow && (
          <p className="text-xs font-semibold uppercase tracking-wider text-brand">{eyebrow}</p>
        )}
        <h1 className="mt-1 text-2xl font-bold tracking-tight sm:text-3xl">{title}</h1>
        {subtitle && <div className="mt-1 text-sm text-muted">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Card({
  title,
  description,
  actions,
  children,
  className,
  id,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  const headingId = id ? `${id}-heading` : undefined;
  return (
    <section
      aria-labelledby={title ? headingId : undefined}
      className={cx(
        "rounded-xl border border-line bg-surface p-4 shadow-card sm:p-5",
        className,
      )}
    >
      {(title || actions) && (
        <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
          <div>
            {title && (
              <h2 id={headingId} className="text-base font-semibold">
                {title}
              </h2>
            )}
            {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function StatTile({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "brand" | "accent" | "gold";
}) {
  const toneClass = {
    default: "text-fg",
    brand: "text-brand",
    accent: "text-accent",
    gold: "text-gold",
  }[tone];
  return (
    <div className="rounded-xl border border-line bg-surface p-4 shadow-card">
      <dt className="text-xs font-medium uppercase tracking-wide text-muted">{label}</dt>
      <dd className={cx("mt-1 text-2xl font-bold tabular-nums", toneClass)}>{value}</dd>
      {hint && <dd className="mt-0.5 text-xs text-muted">{hint}</dd>}
    </div>
  );
}

export function StatGrid({ children, label }: { children: ReactNode; label: string }) {
  return (
    <dl aria-label={label} className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {children}
    </dl>
  );
}

export function Badge({
  children,
  tone = "default",
}: {
  children: ReactNode;
  tone?: "default" | "brand" | "accent" | "gold";
}) {
  const toneClass = {
    default: "bg-surface-2 text-muted",
    brand: "bg-brand-soft text-brand",
    accent: "bg-accent-soft text-accent",
    gold: "bg-warn-soft text-gold",
  }[tone];
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        toneClass,
      )}
    >
      {children}
    </span>
  );
}

export function Medal({ rank }: { rank: number }) {
  if (rank > 3) return <span className="tabular-nums text-muted">{rank}</span>;
  const tone = rank === 1 ? "bg-gold text-on-brand" : rank === 2 ? "bg-muted text-bg" : "bg-accent text-on-brand";
  return (
    <span
      className={cx(
        "inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold",
        tone,
      )}
      aria-label={`Rank ${rank}`}
    >
      {rank}
    </span>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted">
      {children}
    </p>
  );
}

export function ErrorBanner({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <p
      role="alert"
      className="rounded-lg border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger"
    >
      {error}
    </p>
  );
}

export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" }) {
  return (
    <div
      className={cx(
        "rounded-lg px-4 py-3 text-sm",
        tone === "warn" ? "bg-warn-soft text-fg" : "bg-brand-soft text-fg",
      )}
    >
      {children}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx("animate-pulse rounded-lg bg-surface-2", className)} />;
}

export function LoadingBlock({ label = "Loading…", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-2">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-8" />
      ))}
    </div>
  );
}

export function Select<T extends string | number>({
  label,
  value,
  options,
  onChange,
  allLabel,
}: {
  label: string;
  value: T | "";
  options: { value: T; label: string }[];
  onChange: (value: T | "") => void;
  allLabel?: string;
}) {
  return (
    <label className="flex min-w-[8rem] flex-col gap-1 text-xs font-medium text-muted">
      {label}
      <select
        className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg"
        value={value}
        onChange={(event) => {
          const raw = event.target.value;
          if (raw === "") return onChange("");
          const match = options.find((o) => String(o.value) === raw);
          onChange(match ? match.value : "");
        }}
      >
        {allLabel !== undefined && <option value="">{allLabel}</option>}
        {options.map((o) => (
          <option key={String(o.value)} value={String(o.value)}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function ChipGroup<T extends string | number>({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: { value: T; label: string }[];
  selected: T[];
  onChange: (next: T[]) => void;
}) {
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="text-xs font-medium text-muted">{label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => {
          const on = selected.includes(o.value);
          return (
            <button
              key={String(o.value)}
              type="button"
              aria-pressed={on}
              onClick={() =>
                onChange(on ? selected.filter((v) => v !== o.value) : [...selected, o.value])
              }
              className={cx(
                "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                on
                  ? "border-brand bg-brand text-on-brand"
                  : "border-line bg-surface text-muted hover:text-fg",
              )}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
  label,
}: {
  tabs: { value: T; label: string }[];
  active: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex rounded-lg bg-surface-2 p-1">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          type="button"
          aria-selected={active === t.value}
          onClick={() => onChange(t.value)}
          className={cx(
            "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
            active === t.value ? "bg-surface text-fg shadow-card" : "text-muted hover:text-fg",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export interface Column<Row> {
  key: string;
  header: ReactNode;
  cell: (row: Row, index: number) => ReactNode;
  align?: "left" | "right" | "center";
  className?: string;
}

export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  caption,
  highlight,
  empty = "No rows.",
  dense = false,
}: {
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row, index: number) => string;
  caption?: string;
  highlight?: (row: Row) => boolean;
  empty?: ReactNode;
  dense?: boolean;
}) {
  if (rows.length === 0) return <EmptyState>{empty}</EmptyState>;
  const pad = dense ? "px-2 py-1.5" : "px-3 py-2";
  return (
    <div className="-mx-4 overflow-x-auto sm:mx-0">
      <table className="w-full min-w-[32rem] border-collapse text-left text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr className="border-b border-line text-xs uppercase tracking-wide text-muted">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={cx(
                  pad,
                  "font-semibold",
                  c.align === "right" && "text-right",
                  c.align === "center" && "text-center",
                )}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={rowKey(row, i)}
              className={cx(
                "border-b border-line/60 last:border-0",
                highlight?.(row) ? "bg-brand-soft" : "hover:bg-surface-2",
              )}
            >
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={cx(
                    pad,
                    "tabular-nums",
                    c.align === "right" && "text-right",
                    c.align === "center" && "text-center",
                    c.className,
                  )}
                >
                  {c.cell(row, i)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EntityLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} className="font-medium text-fg hover:text-brand hover:underline">
      {children}
    </a>
  );
}

export function athleteHref(id: string): string {
  return `/athletes?id=${encodeURIComponent(id)}`;
}

export function schoolHref(id: string): string {
  return `/schools?id=${encodeURIComponent(id)}`;
}
