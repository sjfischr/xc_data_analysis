// Minimal, safe Markdown for agent answers (Task 19.2). Builds React
// elements only -- never innerHTML -- so nothing in an answer can become
// markup or script. Supports what the agent is told to write: paragraphs,
// headings, bold/italic/inline code, bullet and numbered lists, tables,
// block quotes, rules, and links (http(s) and same-site paths only).
//
// The server sanitizer HTML-escapes the answer before it leaves the API
// (Requirement 10.8); since this renderer treats everything as text, those
// entities are decoded back to characters here.

import type { ReactNode } from "react";

const ENTITIES: Record<string, string> = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#x27;": "'",
  "&#39;": "'",
  "&#x2F;": "/",
};

export function decodeEntities(text: string): string {
  return text.replace(/&(?:amp|lt|gt|quot|#x27|#39|#x2F);/g, (m) => ENTITIES[m] ?? m);
}

function safeHref(href: string): string | null {
  if (href.startsWith("/") && !href.startsWith("//")) return href;
  try {
    const url = new URL(href);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

const INLINE = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;

function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const match of text.matchAll(INLINE)) {
    const token = match[0];
    const index = match.index ?? 0;
    if (index > last) out.push(text.slice(last, index));
    const key = `${keyPrefix}-${i++}`;
    if (token.startsWith("**") || token.startsWith("__")) {
      out.push(<strong key={key}>{inline(token.slice(2, -2), key)}</strong>);
    } else if (token.startsWith("`")) {
      out.push(
        <code key={key} className="rounded bg-surface-2 px-1 py-0.5 text-[0.9em]">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith("[")) {
      const [, label = "", href = ""] = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token) ?? [];
      const safe = safeHref(href);
      out.push(
        safe ? (
          <a key={key} href={safe} className="text-brand underline" rel="noopener noreferrer" target={safe.startsWith("/") ? undefined : "_blank"}>
            {label}
          </a>
        ) : (
          label
        ),
      );
    } else {
      out.push(<em key={key}>{inline(token.slice(1, -1), key)}</em>);
    }
    last = index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
}

const TABLE_DIVIDER = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

export function Markdown({ text }: { text: string }) {
  const lines = decodeEntities(text).replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i] ?? "";
    const k = `b${key++}`;

    if (!line.trim()) {
      i++;
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1]?.length ?? 3;
      const cls = level <= 2 ? "text-lg font-semibold" : "text-base font-semibold";
      blocks.push(
        <p key={k} role="heading" aria-level={Math.min(level + 2, 6)} className={`${cls} mt-2`}>
          {inline(heading[2] ?? "", k)}
        </p>,
      );
      i++;
      continue;
    }

    if (/^(-{3,}|\*{3,})\s*$/.test(line)) {
      blocks.push(<hr key={k} className="border-line" />);
      i++;
      continue;
    }

    if (line.includes("|") && TABLE_DIVIDER.test(lines[i + 1] ?? "")) {
      const header = splitRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && (lines[i] ?? "").includes("|") && (lines[i] ?? "").trim()) {
        rows.push(splitRow(lines[i] ?? ""));
        i++;
      }
      blocks.push(
        <div key={k} className="-mx-1 overflow-x-auto">
          <table className="w-full min-w-[20rem] border-collapse text-left text-sm tabular-nums">
            <thead>
              <tr className="border-b border-line text-xs uppercase tracking-wide text-muted">
                {header.map((cell, c) => (
                  <th key={c} scope="col" className="px-2 py-1.5 font-semibold">
                    {inline(cell, `${k}h${c}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, r) => (
                <tr key={r} className="border-b border-line/60 last:border-0">
                  {row.map((cell, c) => (
                    <td key={c} className="px-2 py-1.5">
                      {inline(cell, `${k}r${r}c${c}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (/^\s*([-*•])\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: string[] = [];
      while (
        i < lines.length &&
        (ordered ? /^\s*\d+[.)]\s+/ : /^\s*([-*•])\s+/).test(lines[i] ?? "")
      ) {
        items.push((lines[i] ?? "").replace(ordered ? /^\s*\d+[.)]\s+/ : /^\s*([-*•])\s+/, ""));
        i++;
      }
      const ListTag = ordered ? "ol" : "ul";
      blocks.push(
        <ListTag key={k} className={`${ordered ? "list-decimal" : "list-disc"} space-y-1 pl-5`}>
          {items.map((item, n) => (
            <li key={n}>{inline(item, `${k}i${n}`)}</li>
          ))}
        </ListTag>,
      );
      continue;
    }

    if (line.startsWith(">")) {
      const quoted: string[] = [];
      while (i < lines.length && (lines[i] ?? "").startsWith(">")) {
        quoted.push((lines[i] ?? "").replace(/^>\s?/, ""));
        i++;
      }
      blocks.push(
        <blockquote key={k} className="border-l-2 border-brand pl-3 text-muted">
          {inline(quoted.join(" "), k)}
        </blockquote>,
      );
      continue;
    }

    const paragraph: string[] = [];
    while (
      i < lines.length &&
      (lines[i] ?? "").trim() &&
      !/^(#{1,4})\s|^\s*([-*•])\s|^\s*\d+[.)]\s|^>/.test(lines[i] ?? "") &&
      !((lines[i] ?? "").includes("|") && TABLE_DIVIDER.test(lines[i + 1] ?? ""))
    ) {
      paragraph.push(lines[i] ?? "");
      i++;
    }
    blocks.push(
      <p key={k} className="leading-relaxed">
        {inline(paragraph.join(" "), k)}
      </p>,
    );
  }

  return <div className="flex flex-col gap-3 text-[0.95rem]">{blocks}</div>;
}
