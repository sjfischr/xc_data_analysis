"use client";

// Ask the Data (Task 19.2). A conversation with the analytics agent:
// answers stream in as they are written, with live tool activity, inline
// interactive charts, sandboxed-Python output (code collapsed, results
// shown), provenance, and clickable follow-ups. `?q=` pre-asks a question
// (the "Ask about this athlete" links elsewhere use it).

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { Markdown } from "@/components/Markdown";
import { Badge, Card, cx, ErrorBanner, LoadingBlock, PageHeader } from "@/components/ui";
import { VegaChart } from "@/components/VegaChart";
import { ApiError } from "@/lib/api";
import {
  chatStatus,
  deleteChatMemory,
  newSessionId,
  streamChat,
  type ChatEvent,
  type ChatStatus,
} from "@/lib/chat";
import type { VisualizationSpec } from "vega-embed";

interface ToolActivity {
  id: string;
  label: string;
  status: "running" | "done" | "error";
}

interface Turn {
  id: string;
  prompt: string;
  state: "streaming" | "done" | "error";
  streamed: string;
  final: string | null;
  tools: ToolActivity[];
  charts: { title: string; spec: Record<string, unknown> }[];
  images: { title: string; src: string }[];
  code: Extract<ChatEvent, { type: "code" }>[];
  followUps: string[];
  meta: Extract<ChatEvent, { type: "complete" }> | null;
  error: string | null;
}

const STARTERS: { group: string; questions: string[] }[] = [
  {
    group: "Athletes",
    questions: [
      "How has Audrey Walker's pace changed over her races? Is she improving?",
      "Compare Sienna Anderson and Audrey Walker's 2025 season head to head.",
    ],
  },
  {
    group: "Standings & teams",
    questions: [
      "Who leads the 2025 Saint Sebastian standings in each division?",
      "Which school won the most team races in 2025? Show it as a chart.",
    ],
  },
  {
    group: "Trends & statistics",
    questions: [
      "Who were the most improved JV runners in 2025, by pace?",
      "Is there a statistically significant relationship between grade and pace for 2025 Varsity girls?",
    ],
  },
  {
    group: "What if",
    questions: [
      "In 2024 Meet 1 Varsity boys, would St James still have won without their fastest runner?",
    ],
  },
];

export default function AskPage() {
  return (
    <AppShell>
      <Suspense fallback={<LoadingBlock />}>
        <AskContent />
      </Suspense>
    </AppShell>
  );
}

function AskContent() {
  const params = useSearchParams();
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState(() => newSessionId());
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const askedFromUrl = useRef(false);
  const busy = turns.some((t) => t.state === "streaming");

  useEffect(() => {
    chatStatus()
      .then(setStatus)
      .catch((e) => setStatusError(String(e)));
  }, []);

  const update = useCallback((id: string, fn: (turn: Turn) => Turn) => {
    setTurns((all) => all.map((t) => (t.id === id ? fn(t) : t)));
  }, []);

  const ask = useCallback(
    async (prompt: string) => {
      const text = prompt.trim();
      if (!text || busy) return;
      const id = newSessionId();
      setInput("");
      setTurns((all) => [
        ...all,
        {
          id,
          prompt: text,
          state: "streaming",
          streamed: "",
          final: null,
          tools: [],
          charts: [],
          images: [],
          code: [],
          followUps: [],
          meta: null,
          error: null,
        },
      ]);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await streamChat(
          sessionId,
          text,
          (event) => update(id, (turn) => applyEvent(turn, event)),
          controller.signal,
        );
        update(id, (turn) => ({ ...turn, state: turn.state === "streaming" ? "done" : turn.state }));
      } catch (error) {
        const aborted = error instanceof DOMException && error.name === "AbortError";
        const message = aborted
          ? "Stopped."
          : error instanceof ApiError && error.status === 403
            ? "Your account doesn't have access to Ask the Data. Ask an administrator to add you."
            : error instanceof ApiError && error.status === 503
              ? "Ask the Data is turned off right now."
              : "Couldn't reach the assistant. Please try again.";
        update(id, (turn) => ({ ...turn, state: aborted ? "done" : "error", error: message }));
      } finally {
        abortRef.current = null;
      }
    },
    [busy, sessionId, update],
  );

  useEffect(() => {
    const q = params.get("q");
    if (q && status?.available && !askedFromUrl.current) {
      askedFromUrl.current = true;
      void ask(q);
    }
  }, [params, status, ask]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, turns.at(-1)?.final, turns.at(-1)?.charts.length]);

  if (statusError) return <ErrorBanner error={statusError} />;
  if (!status) return <LoadingBlock label="Loading Ask the Data" />;

  if (!status.available) {
    return (
      <>
        <PageHeader eyebrow="Ask the Data" title="Not available" />
        <Card>
          <p className="text-sm">
            {!status.enabled
              ? "Ask the Data is turned off right now. Please check back later."
              : "Your account doesn't have access to Ask the Data yet. An administrator can add you to the agent-users group."}
          </p>
        </Card>
      </>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
      <PageHeader
        eyebrow="Ask the Data"
        title="Ask about athletes, schools, and races"
        subtitle="Answers come only from the league's published results. Charts and calculations are built from that data, and every answer says what it used."
        actions={
          <>
            <button
              type="button"
              disabled={busy || turns.length === 0}
              onClick={() => {
                setTurns([]);
                setSessionId(newSessionId());
                setNotice(null);
              }}
              className="rounded-lg border border-line px-3 py-1.5 text-sm font-medium hover:bg-surface-2 disabled:opacity-50"
            >
              New conversation
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                deleteChatMemory()
                  .then((n) => {
                    setTurns([]);
                    setSessionId(newSessionId());
                    setNotice(`Deleted your saved conversation history (${n} exchange${n === 1 ? "" : "s"}).`);
                  })
                  .catch(() => setNotice("Couldn't delete your history. Please try again."))
              }
              className="rounded-lg px-3 py-1.5 text-sm text-muted hover:bg-surface-2 hover:text-fg disabled:opacity-50"
            >
              Delete my history
            </button>
          </>
        }
      />

      {notice && (
        <p role="status" className="text-sm text-muted">
          {notice}
        </p>
      )}

      {turns.length === 0 && (
        <div className="grid gap-4 sm:grid-cols-2">
          {STARTERS.map((group) => (
            <Card key={group.group} title={group.group}>
              <ul className="flex flex-col gap-2">
                {group.questions.map((q) => (
                  <li key={q}>
                    <button
                      type="button"
                      onClick={() => void ask(q)}
                      className="w-full rounded-lg bg-surface-2 px-3 py-2 text-left text-sm hover:bg-brand-soft hover:text-brand"
                    >
                      {q}
                    </button>
                  </li>
                ))}
              </ul>
            </Card>
          ))}
        </div>
      )}

      <ol className="flex flex-col gap-6" aria-live="polite" aria-busy={busy}>
        {turns.map((turn, index) => (
          <li key={turn.id} className="flex flex-col gap-3">
            <div className="self-end rounded-2xl rounded-br-sm bg-brand px-4 py-2.5 text-on-brand shadow-card sm:max-w-[80%]">
              {turn.prompt}
            </div>
            <AnswerCard
              turn={turn}
              isLast={index === turns.length - 1}
              onFollowUp={(q) => void ask(q)}
              busy={busy}
            />
          </li>
        ))}
      </ol>
      <div ref={bottomRef} />

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void ask(input);
        }}
        className="sticky bottom-4 z-20 flex items-end gap-2 rounded-2xl border border-line bg-surface p-2 shadow-card"
      >
        <label htmlFor="ask-input" className="sr-only">
          Your question
        </label>
        <textarea
          id="ask-input"
          rows={1}
          value={input}
          maxLength={2000}
          placeholder={turns.length ? "Ask a follow-up…" : "Ask a question about the results…"}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void ask(input);
            }
          }}
          className="max-h-40 min-h-[2.5rem] flex-1 resize-none bg-transparent px-2 py-2 text-base outline-none placeholder:text-muted"
        />
        {busy ? (
          <button
            type="button"
            onClick={() => abortRef.current?.abort()}
            className="rounded-xl border border-line px-4 py-2 text-sm font-semibold hover:bg-surface-2"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={!input.trim()}
            className="rounded-xl bg-brand px-4 py-2 text-sm font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-40"
          >
            Ask
          </button>
        )}
      </form>
    </div>
  );
}

function applyEvent(turn: Turn, event: ChatEvent): Turn {
  switch (event.type) {
    case "text":
      return { ...turn, streamed: turn.streamed + event.delta };
    case "tool": {
      const existing = turn.tools.find((t) => t.id === event.id);
      if (existing) {
        return {
          ...turn,
          tools: turn.tools.map((t) => (t.id === event.id ? { ...t, status: event.status } : t)),
        };
      }
      return {
        ...turn,
        tools: [...turn.tools, { id: event.id, label: event.label ?? event.name ?? "Working", status: event.status }],
      };
    }
    case "chart":
      return { ...turn, charts: [...turn.charts, { title: event.title, spec: event.spec }] };
    case "image":
      return { ...turn, images: [...turn.images, { title: event.title, src: event.src }] };
    case "code":
      return { ...turn, code: [...turn.code, event] };
    case "follow_ups":
      return { ...turn, followUps: event.questions };
    case "final":
      return { ...turn, final: event.markdown };
    case "complete":
      return { ...turn, meta: event, state: "done" };
    case "error":
      return { ...turn, state: "error", error: event.message };
    default:
      return turn;
  }
}

function AnswerCard({
  turn,
  isLast,
  onFollowUp,
  busy,
}: {
  turn: Turn;
  isLast: boolean;
  onFollowUp: (q: string) => void;
  busy: boolean;
}) {
  const working = turn.state === "streaming";
  const visibleTools = turn.tools.filter((t) => t.label !== "Suggesting follow-ups");
  const running = visibleTools.find((t) => t.status === "running");
  return (
    <div className="flex flex-col gap-4 rounded-2xl rounded-bl-sm border border-line bg-surface p-4 shadow-card sm:p-5">
      {visibleTools.length > 0 && (
        <details className="group text-xs text-muted" open={working}>
          <summary className="flex cursor-pointer list-none items-center gap-2">
            <span
              aria-hidden
              className={cx("inline-block h-2 w-2 rounded-full", working ? "animate-pulse bg-brand" : "bg-line")}
            />
            {working && running ? `${running.label}…` : `${visibleTools.length} step${visibleTools.length === 1 ? "" : "s"}`}
            <span className="ml-auto group-open:hidden">Show steps</span>
          </summary>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {visibleTools.map((t) => (
              <li key={t.id}>
                <Badge tone={t.status === "error" ? "accent" : t.status === "running" ? "brand" : "default"}>
                  {t.status === "done" ? "✓ " : t.status === "error" ? "! " : "… "}
                  {t.label}
                </Badge>
              </li>
            ))}
          </ul>
        </details>
      )}

      {turn.final !== null ? (
        <Markdown text={turn.final} />
      ) : working ? (
        turn.streamed ? (
          <div className="whitespace-pre-wrap text-[0.95rem] text-muted">{turn.streamed}</div>
        ) : (
          <p role="status" className="text-sm text-muted">
            Thinking…
          </p>
        )
      ) : null}

      {turn.charts.map((chart, i) => (
        <div key={i} className="rounded-xl border border-line p-3">
          <VegaChart
            spec={chart.spec as VisualizationSpec}
            height={260}
            label={`Chart: ${chart.title}`}
          />
        </div>
      ))}

      {turn.images.map((image, i) => (
        // eslint-disable-next-line @next/next/no-img-element -- a data URI from the sandbox; next/image needs a server
        <img key={i} src={image.src} alt={`Chart generated by the analysis: ${image.title}`} className="max-w-full rounded-xl border border-line bg-white" />
      ))}

      {turn.code.map((run, i) => (
        <details key={i} className="rounded-xl border border-line text-sm">
          <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted">
            Python analysis {run.ok ? "" : "(failed)"} — show code and output
          </summary>
          <div className="flex flex-col gap-2 border-t border-line p-3">
            {run.sql && (
              <pre className="overflow-x-auto rounded-lg bg-surface-2 p-2 text-xs">
                <code>{run.sql}</code>
              </pre>
            )}
            <pre className="overflow-x-auto rounded-lg bg-surface-2 p-2 text-xs">
              <code>{run.code}</code>
            </pre>
            {(run.stdout || run.stderr) && (
              <pre className="max-h-64 overflow-auto rounded-lg bg-surface-2 p-2 text-xs text-muted">
                {run.stdout}
                {run.stderr}
              </pre>
            )}
          </div>
        </details>
      ))}

      {turn.error && (
        <p role="alert" className="text-sm text-danger">
          {turn.error}
        </p>
      )}

      {isLast && turn.followUps.length > 0 && !working && (
        <div className="flex flex-wrap gap-2">
          {turn.followUps.map((q) => (
            <button
              key={q}
              type="button"
              disabled={busy}
              onClick={() => onFollowUp(q)}
              className="rounded-full border border-brand/40 px-3 py-1.5 text-left text-xs font-medium text-brand hover:bg-brand-soft disabled:opacity-50"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {turn.meta && (
        <p className="border-t border-line pt-2 text-[11px] text-muted">
          Data version {turn.meta.publication_id.slice(0, 8)}
          {turn.meta.model_id ? ` · ${modelLabel(turn.meta.model_id)}` : ""} · {turn.meta.usage.cycles} step
          {turn.meta.usage.cycles === 1 ? "" : "s"}
          {turn.meta.stop_reason.startsWith("limit") ? " · stopped at the step limit" : ""}
        </p>
      )}
    </div>
  );
}

function modelLabel(modelId: string): string {
  if (modelId.includes("haiku")) return "Claude Haiku";
  if (modelId.includes("sonnet")) return "Claude Sonnet";
  if (modelId.includes("opus")) return "Claude Opus";
  return modelId;
}
