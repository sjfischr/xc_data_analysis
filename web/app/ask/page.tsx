"use client";

// Ask the Data (Task 19.2). A conversation with the analytics agent:
// durable jobs replay as they are written, with live tool activity, inline
// interactive charts, sandboxed-Python output (code collapsed, results
// shown), provenance, and clickable follow-ups. `?q=` pre-asks a question
// (the "Ask about this athlete" links elsewhere use it).

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppShell, useCurrentSession } from "@/components/AppShell";
import { Markdown } from "@/components/Markdown";
import { Badge, Card, cx, ErrorBanner, LoadingBlock, PageHeader } from "@/components/ui";
import { VegaChart } from "@/components/VegaChart";
import { api, ApiError } from "@/lib/api";
import {
  chatStatus,
  cancelChatJob,
  compareChatJobs,
  conversationStorageKey,
  createChatJob,
  deleteChatMemory,
  isActiveJob,
  isTransientChatError,
  listChatJobs,
  newSessionId,
  savedConversationId,
  stopReasonLabel,
  waitForChatPoll,
  chatPollDelay,
  watchChatJob,
  type ChatEvent,
  type ChatJob,
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
  state: "streaming" | "done" | "error" | "uncertain";
  reconnecting: boolean;
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
  const { actor_id } = useCurrentSession();
  return <AccountConversation key={actor_id} actorId={actor_id} />;
}

function emptyTurn(id: string, prompt: string): Turn {
  return {
    id, prompt, state: "streaming", reconnecting: false, streamed: "", final: null,
    tools: [], charts: [], images: [], code: [], followUps: [], meta: null, error: null,
  };
}

function jobState(turn: Turn, job: ChatJob): Turn {
  return {
    ...turn,
    state: job.state === "pending"
      ? "uncertain"
      : isActiveJob(job) ? "streaming" : job.state === "failed" ? "error" : "done",
    error: job.error ?? (job.state === "cancelled" ? "Stopped." : turn.error),
  };
}

function reconcileTurns(previous: Turn[], jobs: ChatJob[]): Turn[] {
  return [...jobs].sort(compareChatJobs).map((job) => {
    const turn = previous.find((turn) => turn.id === job.job_id) ?? emptyTurn(job.job_id, job.prompt);
    return {
      ...turn,
      prompt: job.prompt,
      state: job.state === "pending" ? "uncertain" : turn.state,
      error: job.state === "pending"
        ? "This saved job is pending. You can retry safely using its original job ID and question."
        : turn.error,
    };
  });
}

function AccountConversation({ actorId }: { actorId: string }) {
  const params = useSearchParams();
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(true);
  const [locked, setLocked] = useState(false);
  const [checkingAccount, setCheckingAccount] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const lifetime = useRef<AbortController | null>(null);
  const watchers = useRef(new Map<string, AbortController>());
  const submitting = useRef(new Set<string>());
  const generation = useRef(0);
  const cursors = useRef(new Map<string, number>());
  const bottomRef = useRef<HTMLDivElement>(null);
  const askedFromUrl = useRef(false);
  const busy = restoring || deleting || turns.some((t) => t.state === "streaming" || t.state === "uncertain");

  const update = useCallback((id: string, fn: (turn: Turn) => Turn) => {
    setTurns((all) => all.map((t) => (t.id === id ? fn(t) : t)));
  }, []);

  const verifyAccount = useCallback(async () => {
    const signal = lifetime.current?.signal;
    setCheckingAccount(true);
    let failures = 0;
    while (!signal?.aborted) {
      try {
        const session = await api.session();
        if (signal?.aborted) return false;
        if (session.actor_id === actorId) {
          setCheckingAccount(false);
          return true;
        }
        break;
      } catch (error) {
        if (signal?.aborted) return false;
        if (!isTransientChatError(error) || !signal) break;
        try {
          await waitForChatPoll(chatPollDelay(++failures), signal);
        } catch {
          return false;
        }
      }
    }
    if (signal?.aborted) return false;
    setLocked(true);
    setTurns([]);
    setInput("");
    setNotice(null);
    lifetime.current?.abort();
    watchers.current.forEach((controller) => controller.abort());
    return false;
  }, [actorId]);

  const watch = useCallback((id: string) => {
    if (watchers.current.has(id) || lifetime.current?.signal.aborted) return;
    const controller = new AbortController();
    watchers.current.set(id, controller);
    void watchChatJob(id, (page) => {
      cursors.current.set(id, page.next_cursor);
      update(id, (turn) => {
        const baseline = turn.state === "uncertain" && page.job.state !== "pending" ? { ...turn, error: null } : turn;
        const replayed = page.events.reduce(applyEvent, baseline);
        return page.has_more ? { ...replayed, state: "streaming" } : jobState(replayed, page.job);
      });
    }, (reconnecting) => update(id, (turn) => ({ ...turn, reconnecting })),
    controller.signal, cursors.current.get(id) ?? 0)
      .catch((error) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          void verifyAccount();
        }
        update(id, (turn) => ({ ...turn, reconnecting: true }));
      })
      .finally(() => {
        if (watchers.current.get(id) === controller) watchers.current.delete(id);
      });
  }, [update, verifyAccount]);

  const reloadJobs = useCallback(async (conversationId: string, signal: AbortSignal) => {
    let failures = 0;
    while (!signal.aborted) {
      try {
        const { jobs } = await listChatJobs(conversationId, signal);
        if (signal.aborted) return null;
        const durableIds = new Set(jobs.map((job) => job.job_id));
        watchers.current.forEach((controller, id) => {
          if (!durableIds.has(id)) {
            controller.abort();
            watchers.current.delete(id);
            cursors.current.delete(id);
          }
        });
        setTurns((previous) => reconcileTurns(previous, jobs));
        jobs.forEach((job) => watch(job.job_id));
        return jobs;
      } catch (error) {
        if (signal.aborted) return null;
        if (!isTransientChatError(error)) throw error;
        setNotice("Reconnecting to saved jobs… Background work continues. No questions are resubmitted.");
        await waitForChatPoll(chatPollDelay(++failures), signal);
      }
    }
    return null;
  }, [watch]);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    let id: string;
    try {
      id = savedConversationId(localStorage, actorId);
    } catch {
      id = newSessionId();
      setNotice("Browser storage is unavailable; this conversation cannot reconnect after refresh.");
    }
    setSessionId(id);
    void (async () => {
      try {
        const availability = await chatStatus();
        if (controller.signal.aborted) return;
        setStatus(availability);
        const jobs = await reloadJobs(id, controller.signal);
        if (jobs) setNotice(jobs.some(isActiveJob) ? "Reconnected to your background work. You can leave this page; jobs continue." : null);
      } catch (error) {
        if (!controller.signal.aborted) setStatusError(String(error));
      } finally {
        if (!controller.signal.aborted) setRestoring(false);
      }
    })();
    const onFocus = () => { void verifyAccount(); };
    const onBlur = () => setCheckingAccount(true);
    window.addEventListener("focus", onFocus);
    window.addEventListener("blur", onBlur);
    return () => {
      controller.abort();
      watchers.current.forEach((watcher) => watcher.abort());
      watchers.current.clear();
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("blur", onBlur);
    };
  }, [actorId, reloadJobs, verifyAccount]);

  const submit = useCallback(async (id: string, prompt: string) => {
    if (!sessionId || submitting.current.has(id)) return;
    submitting.current.add(id);
    const signal = lifetime.current?.signal;
    const version = generation.current;
    try {
      if (!await verifyAccount() || signal?.aborted) return;
      try {
        localStorage.setItem(conversationStorageKey(actorId), sessionId);
      } catch {
        setNotice("Browser storage is unavailable; this conversation cannot reconnect after refresh.");
      }
      update(id, (turn) => ({ ...turn, state: "streaming", error: null }));
      const job = await createChatJob(sessionId, prompt, id, signal);
      if (signal?.aborted || version !== generation.current) return;
      update(id, (turn) => jobState(turn, job));
      watch(id);
    } catch (error) {
      if (signal?.aborted || version !== generation.current) return;
      if (error instanceof ApiError && error.status === 409 && signal) {
        setRestoring(true);
        setNotice(error.message);
        try {
          const jobs = await reloadJobs(sessionId, signal);
          if (signal.aborted || version !== generation.current) return;
          // A rejected request is not a turn. Recover the original question
          // in the composer while reconnecting any accepted background job.
          if (jobs && !jobs.some((job) => job.job_id === id)) setInput(prompt);
          setNotice(error.message);
        } catch (recoveryError) {
          if (signal.aborted || version !== generation.current) return;
          setTurns((all) => all.filter((turn) => turn.id !== id));
          setInput(prompt);
          setNotice(`${error.message} Couldn't reload saved jobs; refresh to reconnect.`);
          if (recoveryError instanceof ApiError && recoveryError.status === 401) void verifyAccount();
        } finally {
          if (!signal.aborted && version === generation.current) setRestoring(false);
        }
        return;
      }
      const uncertain = isTransientChatError(error);
      update(id, (turn) => ({
        ...turn, state: uncertain ? "uncertain" : "error",
        error: uncertain
          ? "The connection was lost while submitting. The job may already be running. Retry safely with the same job ID."
          : error instanceof ApiError ? error.message : "Couldn't submit this question.",
      }));
      if (uncertain) watch(id);
    } finally {
      submitting.current.delete(id);
    }
  }, [actorId, sessionId, reloadJobs, update, watch, verifyAccount]);

  const ask = useCallback(
    async (prompt: string) => {
      const text = prompt.trim();
      if (!text || busy || !status?.available || !sessionId || submitting.current.size) return;
      const id = newSessionId();
      setInput("");
      setTurns((all) => [...all, emptyTurn(id, text)]);
      await submit(id, text);
    },
    [busy, sessionId, status?.available, submit],
  );

  const resetConversation = useCallback((deleted = false) => {
    generation.current++;
    watchers.current.forEach((controller) => controller.abort());
    watchers.current.clear();
    cursors.current.clear();
    setTurns([]);
    setInput("");
    const id = newSessionId();
    try {
      const key = conversationStorageKey(actorId);
      if (deleted) localStorage.removeItem(key);
      else localStorage.setItem(key, id);
    } catch {
      setNotice("Browser storage is unavailable; refresh will not reconnect this conversation.");
    }
    // After deletion, persist a fresh ID only when the next question is asked.
    setSessionId(id);
  }, [actorId]);

  const stop = async () => {
    if (!await verifyAccount()) return;
    const signal = lifetime.current?.signal;
    for (const turn of turns.filter((turn) => turn.state === "streaming" || turn.state === "uncertain")) {
      try {
        await cancelChatJob(turn.id, signal);
        if (!signal?.aborted) watch(turn.id);
      } catch {
        if (!signal?.aborted) setNotice("Couldn't confirm cancellation. Background work may continue; try Stop again.");
      }
    }
  };

  useEffect(() => {
    const q = params.get("q");
    if (q && status?.available && !restoring && !locked && !askedFromUrl.current) {
      askedFromUrl.current = true;
      // A refreshed deep link must not submit its question a second time.
      if (turns.length === 0) void ask(q);
    }
  }, [params, status, ask, restoring, locked, turns.length]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, turns.at(-1)?.final, turns.at(-1)?.charts.length]);

  if (locked) return <ErrorBanner error="Your account changed or signed out. Reload this page to reconnect safely." />;
  if (statusError) return <ErrorBanner error={statusError} />;
  if (!status) return <LoadingBlock label="Loading Ask the Data" />;

  return (
    <>
      {checkingAccount && <LoadingBlock label="Checking your account before reconnecting" />}
      {/* Mask unverified content without changing layout/focus mid-input. */}
      <div aria-hidden={checkingAccount || undefined}
        style={checkingAccount ? { opacity: 0, pointerEvents: "none" } : undefined}
        className="mx-auto flex w-full max-w-4xl flex-col gap-6">
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
                setNotice(null);
                resetConversation();
              }}
              className="rounded-lg border border-line px-3 py-1.5 text-sm font-medium hover:bg-surface-2 disabled:opacity-50"
            >
              New conversation
            </button>
            <button
              type="button"
              disabled={restoring || deleting || submitting.current.size > 0}
              onClick={async () => {
                if (!await verifyAccount()) return;
                setDeleting(true);
                const signal = lifetime.current?.signal;
                try {
                  const n = await deleteChatMemory();
                  if (signal?.aborted) return;
                  resetConversation(true);
                  setNotice(`Deleted your saved conversation history and background jobs (${n} exchange${n === 1 ? "" : "s"}).`);
                } catch (error) {
                  if (!signal?.aborted) setNotice(error instanceof ApiError ? error.message : "Couldn't delete your history. Please try again.");
                } finally {
                  if (!signal?.aborted) setDeleting(false);
                }
              }}
              className="rounded-lg px-3 py-1.5 text-sm text-muted hover:bg-surface-2 hover:text-fg disabled:opacity-50"
            >
              Delete my history
            </button>
          </>
        }
      />

      {!status.available && (
        <Card>
          <p className="text-sm">
            {!status.enabled
              ? "New questions are turned off right now. Saved answers and background jobs are still available."
              : "Your account cannot submit new questions. An administrator can grant access; saved history remains available."}
          </p>
        </Card>
      )}

      {notice && (
        <p role="status" className="text-sm text-muted">
          {notice}
        </p>
      )}

      <p role="status" className="text-xs text-muted">
        {restoring ? "Reconnecting to saved conversation…" : "Questions run in the background. Leaving or refreshing this page does not stop them; return to reconnect."}
      </p>

      {turns.length === 0 && status.available && !restoring && (
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
              busy={busy || !status.available}
            />
            {turn.state === "uncertain" && (
              <button type="button" disabled={!status.available} onClick={() => void submit(turn.id, turn.prompt)}
                className="self-start rounded-lg border border-line px-3 py-2 text-sm">
                Retry submission safely
              </button>
            )}
            {turn.reconnecting && (
              <button type="button" onClick={() => {
                watchers.current.get(turn.id)?.abort();
                watchers.current.delete(turn.id);
                watch(turn.id);
              }}
                className="self-start rounded-lg border border-line px-3 py-2 text-sm">
                Reconnect to job
              </button>
            )}
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
            disabled={restoring || deleting}
            onClick={() => void stop()}
            className="rounded-xl border border-line px-4 py-2 text-sm font-semibold hover:bg-surface-2"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={!input.trim() || !status.available}
            className="rounded-xl bg-brand px-4 py-2 text-sm font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-40"
          >
            Ask
          </button>
        )}
      </form>
      </div>
    </>
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
      ) : turn.streamed ? (
        <div className="whitespace-pre-wrap text-[0.95rem] text-muted">{turn.streamed}</div>
      ) : working ? (
          <p role="status" className="text-sm text-muted">
            Thinking…
          </p>
      ) : null}

      {turn.reconnecting && (
        <p role="status" className="text-sm text-muted">
          Reconnecting… This job may still be running in the background. No question is being resubmitted.
        </p>
      )}

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
          {turn.meta.model_id ? ` · ${modelLabel(turn.meta.model_id)}` : ""}
          {turn.meta.usage.cycles == null ? " · cycles unknown" : ` · ${turn.meta.usage.cycles} cycle${turn.meta.usage.cycles === 1 ? "" : "s"}`}
          {turn.meta.partial || turn.meta.usage.partial ? " · partial usage accounting" : ""}
          {stopReasonLabel(turn.meta.stop_reason) ? ` · ${stopReasonLabel(turn.meta.stop_reason)}` : ""}
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
