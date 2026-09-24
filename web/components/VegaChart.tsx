"use client";

// The one chart renderer (Task 19.0): dashboard pages and the analytics
// agent's chart events both hand a Vega-Lite spec to this component.
//
// - `ast: true` makes vega evaluate expressions with its interpreter
//   instead of `new Function`, so a spec -- including one built from agent
//   output -- never executes generated code, and the page needs no
//   `unsafe-eval` CSP.
// - The theme is read from app/globals.css's CSS variables at render time
//   and re-applied when the OS color scheme changes.
// - vega-embed is imported on demand so pages without charts don't pay
//   for it.

import { useEffect, useRef, useState } from "react";
import type { VisualizationSpec } from "vega-embed";

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

// Vega's SVG renderer writes colors as attributes, where CSS variables do
// not resolve -- so any `var(--token)` string in a spec is swapped for its
// computed value before embedding.
function resolveCssVars<T>(value: T): T {
  if (typeof value === "string") {
    const match = /^var\((--[\w-]+)\)$/.exec(value);
    return (match?.[1] ? cssVar(match[1], value) : value) as T;
  }
  if (Array.isArray(value)) return value.map(resolveCssVars) as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, resolveCssVars(v)]),
    ) as T;
  }
  return value;
}

export function chartPalette(): string[] {
  return [1, 2, 3, 4, 5, 6].map((i) => cssVar(`--chart-${i}`, "#17744b"));
}

function themeConfig() {
  const fg = cssVar("--fg", "#13201a");
  const muted = cssVar("--muted", "#5a6b62");
  const line = cssVar("--line", "#dfe6e1");
  const font =
    'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
  return {
    background: "transparent",
    font,
    view: { stroke: "transparent" },
    range: { category: chartPalette() },
    axis: {
      labelColor: muted,
      titleColor: muted,
      gridColor: line,
      domainColor: line,
      tickColor: line,
      labelFontSize: 11,
      titleFontSize: 11,
      titleFontWeight: 500,
      labelFont: font,
      titleFont: font,
    },
    legend: {
      labelColor: fg,
      titleColor: muted,
      labelFontSize: 11,
      titleFontSize: 11,
      orient: "bottom",
    },
    title: { color: fg, fontSize: 13, fontWeight: 600, anchor: "start" },
    line: { strokeWidth: 2.5 },
    point: { size: 60, filled: true },
  };
}

function useColorScheme(): string {
  const [scheme, setScheme] = useState("light");
  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setScheme(query.matches ? "dark" : "light");
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return scheme;
}

export function VegaChart({
  spec,
  label,
  height = 260,
}: {
  spec: VisualizationSpec;
  /** Required text alternative (Requirement 12.6) -- what the chart shows. */
  label: string;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const scheme = useColorScheme();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    let finalize: (() => void) | undefined;
    let cancelled = false;
    import("vega-embed")
      .then(({ default: embed }) =>
        embed(
          element,
          resolveCssVars({
            width: "container",
            height,
            autosize: { type: "fit", contains: "padding" },
            ...spec,
          }) as VisualizationSpec,
          {
            actions: false,
            renderer: "svg",
            ast: true,
            config: themeConfig() as never,
            tooltip: { theme: "custom" },
          },
        ),
      )
      .then((result) => {
        if (cancelled) result.finalize();
        else finalize = () => result.finalize();
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
      finalize?.();
    };
  }, [spec, height, scheme]);

  return (
    <figure className="w-full">
      <div ref={ref} role="img" aria-label={label} className="w-full" style={{ minHeight: height }} />
      {error && (
        <figcaption role="alert" className="text-xs text-danger">
          Chart failed to render: {error}
        </figcaption>
      )}
    </figure>
  );
}
