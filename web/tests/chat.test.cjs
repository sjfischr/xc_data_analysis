const { test, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

// Use the workspace's locked TypeScript compiler and Node's built-in runner.
// No emitted files, DOM dependencies, real accounts, or live model calls.
function load(relative, imports = {}, extra = "") {
  const filename = path.resolve(__dirname, "..", relative);
  const mod = new Module(filename, module);
  mod.filename = filename;
  mod.paths = module.paths;
  mod.require = (name) => name in imports ? imports[name] : require(name);
  const source = fs.readFileSync(filename, "utf8") + extra;
  mod._compile(ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, filename);
  return mod.exports;
}

const api = load("lib\\api.ts");
const chat = load("lib\\chat.ts", { "./api": api });
const plain = ({ children }) => React.createElement("div", null, children);
const page = load("app\\ask\\page.tsx", {
  "@/lib/api": api,
  "@/lib/chat": chat,
  "next/navigation": {},
  "@/components/AppShell": {},
  "@/components/Markdown": { Markdown: ({ text }) => React.createElement("div", null, text) },
  "@/components/VegaChart": { VegaChart: ({ label }) => React.createElement("div", null, label) },
  "@/components/ui": { Badge: plain, cx: (...parts) => parts.join(" ") },
}, "\nexport { emptyTurn, jobState, reconcileTurns, applyEvent, AnswerCard };");

const originalFetch = global.fetch;
const originalDocument = global.document;
const originalWindow = global.window;
const originalStorage = global.localStorage;
afterEach(() => {
  global.fetch = originalFetch;
  global.document = originalDocument;
  global.window = originalWindow;
  global.localStorage = originalStorage;
});

// Exercise the real component's state/effects with controlled async session
// checks. JSX ancestry detects the textarea-remount regression without a DOM.
function accountHarness(jobs) {
  const slots = [];
  let index = 0;
  let effects = [];
  const sameDeps = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const hooks = {
    ...React,
    useState(initial) {
      const position = index++;
      if (!(position in slots)) slots[position] = typeof initial === "function" ? initial() : initial;
      return [slots[position], (value) => {
        slots[position] = typeof value === "function" ? value(slots[position]) : value;
      }];
    },
    useRef(initial) {
      const position = index++;
      return slots[position] ?? (slots[position] = { current: initial });
    },
    useCallback(fn, deps) {
      const position = index++;
      if (!sameDeps(slots[position]?.deps, deps)) slots[position] = { deps, fn };
      return slots[position].fn;
    },
    useEffect(fn, deps) {
      const position = index++;
      if (!sameDeps(slots[position]?.deps, deps)) {
        const previous = slots[position];
        slots[position] = { deps };
        effects.push(() => {
          previous?.cleanup?.();
          slots[position].cleanup = fn();
        });
      }
    },
  };
  const listeners = new Map();
  global.window = {
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: (name) => listeners.delete(name),
  };
  const values = new Map();
  global.localStorage = {
    getItem: (key) => values.get(key),
    setItem: (key, value) => values.set(key, value),
  };
  const sessionChecks = [];
  const component = load("app\\ask\\page.tsx", {
    react: hooks,
    "@/lib/api": { ...api, api: { session: () => new Promise((resolve, reject) => { sessionChecks.push({ resolve, reject }); }) } },
    "@/lib/chat": {
      ...chat,
      chatStatus: async () => ({ available: true, enabled: true, access: true }),
      listChatJobs: async () => ({ jobs }),
      waitForChatPoll: async () => {},
      watchChatJob: async (_, onPage) => {
        onPage({ job: job("completed"), events: [
          { type: "final", markdown: "Saved answer" },
          { type: "chart", title: "Saved chart", spec: {} },
        ], next_cursor: 2, has_more: false });
      },
    },
    "next/navigation": { useSearchParams: () => new URLSearchParams() },
    "@/components/AppShell": {},
    "@/components/Markdown": {},
    "@/components/VegaChart": {},
    "@/components/ui": {},
  }, "\nexport { AccountConversation };");
  return {
    render() {
      index = 0;
      const tree = component.AccountConversation({ actorId: "fixture-actor" });
      const pending = effects;
      effects = [];
      pending.forEach((effect) => effect());
      return tree;
    },
    event: (name) => listeners.get(name)?.(),
    resolve: (actorId = "fixture-actor", index = sessionChecks.length - 1) => sessionChecks[index].resolve({ actor_id: actorId }),
    reject: (error, index = sessionChecks.length - 1) => sessionChecks[index].reject(error),
    cleanup: () => slots.forEach((slot) => slot?.cleanup?.()),
  };
}

function findElement(tree, predicate, ancestry = []) {
  if (!tree || typeof tree !== "object") return null;
  if (Array.isArray(tree)) {
    for (const [index, child] of tree.entries()) {
      const found = findElement(child, predicate, [...ancestry, index]);
      if (found) return found;
    }
    return null;
  }
  const path = [...ancestry, [tree.type, tree.key]];
  if (predicate(tree)) return { element: tree, path };
  return findElement(tree.props?.children, predicate, path);
}

test("first fill survives window-focus verification without detaching composer or replayed history", async () => {
  for (const jobs of [[], [job("completed")]]) {
    const harness = accountHarness(jobs);
    try {
      harness.render();
      await new Promise(setImmediate);
      const before = harness.render();
      const textarea = findElement(before, (node) => node.type === "textarea");
      assert.ok(textarea);
      harness.event("blur");
      harness.event("focus");
      const checking = harness.render();
      const pendingInput = findElement(checking, (node) => node.type === "textarea");
      // Pre-fix, the loading-only return removed this node completely.
      assert.ok(pendingInput, "the first fill must not target a detached textarea");
      assert.deepEqual(pendingInput.path, textarea.path, "React reconciliation must retain the same textarea");
      const hidden = findElement(checking, (node) => node.props?.["aria-hidden"] === true);
      assert.equal(hidden.element.props.style.opacity, 0, "unverified youth content remains visually masked");
      assert.equal(hidden.element.props.style.pointerEvents, "none");
      assert.equal(hidden.element.props.style.display, undefined, "the focus target must retain layout during first fill");
      assert.equal(hidden.element.props.hidden, undefined, "hidden/display:none can suppress the first input event");
      textarea.element.props.onChange({ target: { value: "First follow-up fill" } });
      assert.equal(findElement(harness.render(), (node) => node.type === "textarea").element.props.value, "First follow-up fill");
      // Live first-fill focuses the window more than once. Complete the
      // checks out of order, including a transient retry, without resetting
      // the controlled draft that the input event delivered.
      harness.event("focus");
      harness.reject(new TypeError("temporary session fetch failure"));
      await new Promise(setImmediate);
      harness.resolve();
      await new Promise(setImmediate);
      harness.resolve("fixture-actor", 0);
      harness.resolve();
      await new Promise(setImmediate);
      const verified = harness.render();
      const filled = findElement(verified, (node) => node.type === "textarea");
      assert.deepEqual(filled.path, textarea.path);
      assert.equal(filled.element.props.value, "First follow-up fill");
      assert.equal(findElement(verified, (node) => node.type === "button" && node.props.type === "submit").element.props.disabled, false);
      assert.equal(findElement(verified, (node) => node.props?.["aria-hidden"] === true), null);
      if (jobs.length) {
        const answer = findElement(verified, (node) => node.props?.turn);
        assert.equal(answer.element.props.turn.final, "Saved answer");
        assert.equal(answer.element.props.turn.charts.length, 1);
      }
      // A genuinely changed identity still clears and removes the old UI.
      harness.event("focus");
      harness.resolve("different-actor");
      await new Promise(setImmediate);
      assert.equal(findElement(harness.render(), (node) => node.type === "textarea"), null);
    } finally {
      harness.cleanup();
    }
  }
});

function job(state = "running") {
  return {
    job_id: "00000000-0000-4000-8000-000000000001",
    session_id: "00000000-0000-4000-8000-000000000002",
    prompt: "Fixture question", state, created_at: "2026-11-07T12:00:00Z",
    updated_at: "2026-11-07T12:00:00Z", publication_id: "fixture-publication",
    error: null, next_cursor: 0,
  };
}

function response(body, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

test("POST retries reuse the supplied UUID, credentials, and CSRF; no automatic resubmission", async () => {
  global.document = { cookie: "xc_csrf=fixture-token" };
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    if (calls.length === 1) throw new TypeError("connection lost after acceptance");
    return response(job());
  };
  const fixture = job();
  await assert.rejects(chat.createChatJob(fixture.session_id, fixture.prompt, fixture.job_id));
  assert.equal(calls.length, 1);
  await chat.createChatJob(fixture.session_id, fixture.prompt, fixture.job_id);
  assert.deepEqual(calls.map((call) => JSON.parse(call.options.body)), [
    { session_id: fixture.session_id, prompt: fixture.prompt, job_id: fixture.job_id },
    { session_id: fixture.session_id, prompt: fixture.prompt, job_id: fixture.job_id },
  ]);
  for (const { url, options } of calls) {
    assert.ok(url.endsWith("/api/v1/chat/jobs"));
    assert.equal(options.credentials, "include");
    assert.equal(options.headers["X-CSRF-Token"], "fixture-token");
    assert.equal(options.cache, "no-store");
  }
});

test("history, explicit cancellation, and memory deletion use durable endpoints", async () => {
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    return response(url.endsWith("/memory") ? { deleted_events: 2 } : job("cancelled"));
  };
  await chat.listChatJobs("a b");
  await chat.cancelChatJob(job().job_id);
  assert.equal(await chat.deleteChatMemory(), 2);
  assert.ok(calls[0].url.endsWith("/jobs?session_id=a%20b"));
  assert.ok(calls[1].url.endsWith(`/jobs/${job().job_id}/cancel`));
  assert.equal(calls[1].options.method, "POST");
  assert.equal(calls[2].options.method, "DELETE");
});

test("active-job deletion conflicts preserve the server's explanation", async () => {
  global.fetch = async () => response({
    error: { code: "chat_jobs_active", message: "Stop active jobs before deleting history." },
  }, 409);
  await assert.rejects(chat.deleteChatMemory(), {
    status: 409, code: "chat_jobs_active", message: "Stop active jobs before deleting history.",
  });
});

test("terminal jobs replay every page in order, using only the acknowledged cursor", async () => {
  const requests = [];
  const controller = new AbortController();
  const pages = [
    { job: job("completed"), events: [{ type: "text", delta: "a" }], next_cursor: 100, has_more: true },
    { job: job("completed"), events: [{ type: "text", delta: "b" }, { type: "final", markdown: "ab" }], next_cursor: 102, has_more: false },
  ];
  global.fetch = async (url, options) => {
    assert.equal(options.method, "GET");
    requests.push(url);
    return response(pages.shift());
  };
  const events = [];
  await chat.watchChatJob(job().job_id, (page) => events.push(...page.events), () => {}, controller.signal);
  assert.ok(requests[0].endsWith("?after=0"));
  assert.ok(requests[1].endsWith("?after=100"));
  assert.deepEqual(events.map((event) => event.type), ["text", "text", "final"]);
});

test("transient polling failures back off, never POST, and retain the replay cursor", async () => {
  const urls = [];
  const controller = new AbortController();
  const connections = [];
  let attempts = 0;
  global.fetch = async (url, options) => {
    assert.equal(options.method, "GET");
    urls.push(url);
    if (++attempts === 1) throw new TypeError("offline");
    return response({ job: job("completed"), events: [], next_cursor: 7, has_more: false });
  };
  const start = Date.now();
  await chat.watchChatJob(job().job_id, () => {}, (value) => connections.push(value), controller.signal, 7);
  assert.ok(Date.now() - start >= 1900);
  assert.deepEqual(connections, [true, false]);
  assert.ok(urls.every((url) => url.endsWith("?after=7")));
  assert.deepEqual([0, 1, 2, 20].map(chat.chatPollDelay), [1000, 2000, 4000, 30000]);
});

test("stopping a watcher aborts only GET/polling, not the durable job", async () => {
  const controller = new AbortController();
  const methods = [];
  global.fetch = async (_, options) => {
    methods.push(options.method);
    return response({ job: job(), events: [], next_cursor: 0, has_more: false });
  };
  await assert.rejects(chat.watchChatJob(job().job_id, () => controller.abort(), () => {}, controller.signal),
    { name: "AbortError" });
  assert.deepEqual(methods, ["GET"]);
  assert.equal(chat.isTransientChatError(new api.ApiError(401, "unauthenticated", "no")), false);
  assert.equal(chat.isTransientChatError(new api.ApiError(429, "rate_limit", "later")), true);
});

test("reconnecting resumes from the in-memory cursor without replaying output twice", async () => {
  const requests = [];
  const first = new AbortController();
  const events = [];
  let cursor = 0;
  global.fetch = async (url) => {
    requests.push(url);
    return response({
      job: job(requests.length === 1 ? "running" : "completed"),
      events: [{ type: "text", delta: requests.length === 1 ? "a" : "b" }],
      next_cursor: requests.length, has_more: false,
    });
  };
  await assert.rejects(chat.watchChatJob(job().job_id, (page) => {
    events.push(...page.events);
    cursor = page.next_cursor;
    first.abort();
  }, () => {}, first.signal), { name: "AbortError" });
  await chat.watchChatJob(job().job_id, (page) => events.push(...page.events), () => {},
    new AbortController().signal, cursor);
  assert.ok(requests[1].endsWith("?after=1"));
  assert.equal(events.map((event) => event.delta).join(""), "ab");
});

test("per-account persistence stores only a UUID and survives refresh", () => {
  const values = new Map();
  const storage = { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value) };
  const first = chat.savedConversationId(storage, "actor-a");
  assert.match(first, /^[a-f0-9-]{36}$/);
  assert.equal(chat.savedConversationId(storage, "actor-a"), first);
  assert.notEqual(chat.savedConversationId(storage, "actor-b"), first);
  assert.ok([...values.values()].every((value) => /^[a-f0-9-]{36}$/.test(value)));
});

test("Unix-second and ISO timestamps both sort durable history chronologically", () => {
  const older = { ...job(), created_at: 100, job_id: "a" };
  const newer = { ...job(), created_at: 101, job_id: "b" };
  assert.deepEqual([newer, older].sort(chat.compareChatJobs).map((job) => job.job_id), ["a", "b"]);
  assert.equal(chat.compareChatJobs(older, { ...older, created_at: "1970-01-01T00:01:40Z" }), 0);
});

test("an uncertain pending ACK stays safely retryable, unlike running work", () => {
  const uncertain = { ...page.emptyTurn("id", "prompt"), state: "uncertain", error: "ACK unknown" };
  assert.equal(page.jobState(uncertain, job("pending")).state, "uncertain");
  assert.equal(page.jobState(uncertain, job("running")).state, "streaming");
  assert.equal(page.jobState(uncertain, job("completed")).state, "done");
});

test("network/502 uncertainty refresh recovers pending server ID and prompt for safe retry", async () => {
  for (const failure of ["network", "502"]) {
    const fixture = job("pending");
    const requests = [];
    global.fetch = async (url, options) => {
      requests.push({ url, options });
      if (requests.length === 1) {
        if (failure === "network") throw new TypeError("ACK lost");
        return response({ error: { code: "job_submission_uncertain", message: "ACK unknown" } }, 502);
      }
      if (options.method === "GET") return response({ jobs: [fixture] });
      return response({ ...fixture, state: "running" }, 202);
    };
    await assert.rejects(chat.createChatJob(fixture.session_id, fixture.prompt, fixture.job_id));
    assert.equal(requests.length, 1);
    const history = await chat.listChatJobs(fixture.session_id);
    const restored = page.reconcileTurns([], history.jobs);
    assert.equal(restored[0].state, "uncertain");
    assert.equal(restored[0].id, fixture.job_id);
    assert.equal(restored[0].prompt, fixture.prompt);
    await chat.createChatJob(fixture.session_id, restored[0].prompt, restored[0].id);
    assert.deepEqual(JSON.parse(requests[0].options.body), JSON.parse(requests[2].options.body));
  }
});

test("409 reconciliation removes rejected placeholder and reconnects durable running history", async () => {
  const running = job();
  const existing = page.applyEvent(page.emptyTurn(running.job_id, running.prompt), {
    type: "chart", title: "Already replayed", spec: {},
  });
  const placeholder = page.emptyTurn("rejected-uuid", "Unsaved question");
  let calls = 0;
  global.fetch = async (_, options) => {
    calls++;
    return options.method === "POST"
      ? response({ error: { code: "job_already_running", message: "Another job is running." } }, 409)
      : response({ jobs: [running] });
  };
  await assert.rejects(chat.createChatJob(running.session_id, placeholder.prompt, placeholder.id),
    { status: 409, message: "Another job is running." });
  const { jobs } = await chat.listChatJobs(running.session_id);
  const reconciled = page.reconcileTurns([existing, placeholder], jobs);
  assert.equal(calls, 2);
  assert.deepEqual(reconciled.map((turn) => turn.id), [running.job_id]);
  assert.equal(reconciled[0].charts.length, 1);
  assert.equal(reconciled[0].state, "streaming");
});

test("explicit conversation/actor cap errors survive unchanged", async () => {
  for (const message of ["This conversation has 50 jobs. Start a new conversation.", "You have 200 conversations. Delete history before creating another."]) {
    global.fetch = async () => response({ error: { code: "chat_capacity_exceeded", message } }, 409);
    await assert.rejects(chat.createChatJob(job().session_id, job().prompt, job().job_id),
      { status: 409, message });
  }
});

test("idle completed history can replay when new questions are globally disabled", async () => {
  const requests = [];
  global.fetch = async (url, options) => {
    requests.push({ url, options });
    if (url.endsWith("/status")) return response({ enabled: false, access: true, available: false });
    if (url.includes("?session_id=")) return response({ jobs: [job("completed")] });
    return response({
      job: job("completed"), events: [{ type: "final", markdown: "Saved answer" }],
      next_cursor: 1, has_more: false,
    });
  };
  assert.equal((await chat.chatStatus()).available, false);
  const { jobs } = await chat.listChatJobs(job().session_id);
  let turn = page.reconcileTurns([], jobs)[0];
  await chat.watchChatJob(turn.id, (result) => {
    turn = page.jobState(result.events.reduce(page.applyEvent, turn), result.job);
  }, () => {}, new AbortController().signal);
  assert.equal(turn.state, "done");
  assert.equal(turn.final, "Saved answer");
  assert.ok(requests.every(({ options }) => !options.method || options.method === "GET"));
});

test("all output shapes replay once and remain visible after cancellation", () => {
  const events = [
    { type: "status", status: "running" },
    { type: "text", delta: "Partial answer" },
    { type: "tool", id: "t", label: "Read results", status: "running" },
    { type: "tool", id: "t", status: "done" },
    { type: "chart", title: "Fixture chart", spec: {} },
    { type: "image", title: "Fixture image", src: "data:image/png;base64,AA==" },
    { type: "code", code: "print(1)", language: "python", sql: "select 1", stdout: "1", stderr: "", ok: true },
    { type: "follow_ups", questions: ["Follow up"] },
  ];
  const turn = page.jobState(events.reduce(page.applyEvent, page.emptyTurn("job", "prompt")), job("cancelled"));
  assert.equal(turn.tools.length, 1);
  assert.equal(turn.tools[0].status, "done");
  assert.equal(turn.charts.length, 1);
  assert.equal(turn.images.length, 1);
  assert.equal(turn.code.length, 1);
  const markup = renderToStaticMarkup(React.createElement(page.AnswerCard, {
    turn, isLast: true, busy: false, onFollowUp: () => {},
  }));
  for (const text of ["Partial answer", "Fixture chart", "Fixture image", "print(1)", "select 1", "Follow up", "Stopped."]) {
    assert.ok(markup.includes(text), text);
  }
  const final = page.applyEvent(turn, { type: "final", markdown: "**Fixture answer**" });
  assert.equal(final.final, "**Fixture answer**");
});

test("footer accurately distinguishes time, output tokens, cycles, and unknown usage", () => {
  const labels = [
    ["limit_time", "time limit"], ["limit_max_tokens", "output-token limit"],
    ["limit_cycles", "cycle limit"], ["limit_steps", "step limit"],
  ];
  for (const [reason, expected] of labels) {
    const turn = page.applyEvent(page.emptyTurn("job", "prompt"), {
      type: "complete", stop_reason: reason, publication_id: "publication-123", model_id: null,
      usage: { input_tokens: null, output_tokens: null, cache_read_tokens: null, cache_write_tokens: null, cycles: null, partial: true },
    });
    const markup = renderToStaticMarkup(React.createElement(page.AnswerCard, {
      turn, isLast: true, busy: false, onFollowUp: () => {},
    }));
    assert.ok(markup.includes(expected));
    assert.ok(markup.includes("cycles unknown"));
    assert.ok(markup.includes("partial usage accounting"));
    assert.ok(!markup.includes("0 cycle"));
  }
  assert.equal(chat.stopReasonLabel("end_turn"), null);
  assert.equal(chat.stopReasonLabel("limit_new_control"), "stopped (limit_new_control)");
});
