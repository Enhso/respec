import { type SubmitEvent, useCallback, useEffect, useState } from "react";

type Tails = { openrouter: string | null; gemini: string | null };

const PROVIDERS = [
  { id: "openrouter", label: "OpenRouter key", name: "OpenRouter" },
  { id: "gemini", label: "Google Gemini key", name: "Google Gemini" },
] as const;

type ProviderId = (typeof PROVIDERS)[number]["id"];

type TestCallResult =
  | { ok: true; model: string; reply: string }
  | { ok: false; message: string };

/** `GET /api/test-extraction`: the current or last run, or none yet. */
type Run = {
  status: "idle" | "running" | "done" | "failed";
  elapsed_secs?: number;
  progress?: { stage: string; detail: string }[];
  result?: {
    model: string;
    document: { url: string; title: string | null; chars: number };
    entities: { label: string; name: string; sentence: string }[];
  };
  failure?: { message: string };
};

const CALLING = "calling...";
const POLL_MS = 1500;
const CELL = { padding: "0.5rem 0.75rem", verticalAlign: "top", textAlign: "left" } as const;

/** Throws the server's message when a response is not a success. */
async function ensureOk(res: Response): Promise<void> {
  if (!res.ok) {
    const message = (await res.text()).trim();
    throw new Error(message || `Request failed (${res.status})`);
  }
}

/** Reads the tails from a settings response, or throws the server's message. */
async function readSettings(res: Response): Promise<Tails> {
  await ensureOk(res);
  const body: { keys: Tails } = await res.json();
  return body.keys;
}

/** Makes one test call and describes how it went, in a sentence. */
async function testCall(provider: ProviderId): Promise<string> {
  try {
    const res = await fetch("/api/test-call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    await ensureOk(res);
    const result: TestCallResult = await res.json();
    return result.ok ? `ok: ${result.model} replied: ${result.reply}` : result.message;
  } catch (err) {
    return err instanceof Error ? err.message : String(err);
  }
}

/** The entity Proposals of a finished run, sorted by kind, one per row. */
function Results({ result }: { result: NonNullable<Run["result"]> }) {
  const { document, entities, model } = result;
  const sorted = [...entities].sort((a, b) => a.label.localeCompare(b.label));
  const counts = new Map<string, number>();
  for (const { label } of sorted) counts.set(label, (counts.get(label) ?? 0) + 1);
  return (
    <>
      <p>
        <strong>{document.title ?? document.url}</strong>: {document.chars.toLocaleString()}{" "}
        characters of article text.
      </p>
      <p>
        {entities.length} proposals from {model}
        {entities.length > 0 &&
          ` (${[...counts].map(([label, n]) => `${n} ${label}`).join(", ")})`}
        .
      </p>
      {entities.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
          <colgroup>
            <col style={{ width: "24%" }} />
            <col style={{ width: "12%" }} />
            <col />
          </colgroup>
          <thead>
            <tr style={{ borderBottom: "2px solid #888" }}>
              <th style={CELL}>Name</th>
              <th style={CELL}>Kind</th>
              <th style={CELL}>Sentence</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(({ label, name, sentence }, i) => (
              <tr key={i} style={{ borderBottom: "1px solid #ddd" }}>
                <td style={{ ...CELL, fontWeight: 600 }}>{name}</td>
                <td style={CELL}>{label}</td>
                <td style={{ ...CELL, lineHeight: 1.4 }}>{sentence}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

/** The test extraction form, its progress while it runs and its results. */
function TestExtraction({ providers }: { providers: { id: ProviderId; name: string }[] }) {
  const [url, setUrl] = useState("");
  const [choice, setChoice] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const provider = providers.some((p) => p.id === choice) ? choice : (providers[0]?.id ?? "");
  const running = run?.status === "running";

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/test-extraction");
      await ensureOk(res);
      setRun(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // On load this picks up a run already going, which the next effect then polls.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [running, refresh]);

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const res = await fetch("/api/test-extraction", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, url: url.trim() }),
      });
      await ensureOk(res);
      setRun(await res.json());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      void refresh(); // a 409 means a run is going; show it
    }
  }

  return (
    <section>
      <h2>Test extraction</h2>
      <form onSubmit={onSubmit}>
        <p>
          <label>
            Article address
            <br />
            <input
              type="url"
              required
              placeholder="https://"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              style={{ width: "100%" }}
            />
          </label>
        </p>
        <p>
          <label>
            Provider{" "}
            <select value={provider} onChange={(e) => setChoice(e.target.value)}>
              {providers.map(({ id, name }) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>{" "}
          <button type="submit" disabled={running || !provider || !url.trim()}>
            Run test extraction
          </button>
          {providers.length === 0 && <small> Save a key above first.</small>}
        </p>
      </form>
      {error && <p role="alert">{error}</p>}
      {running && (
        <p role="status">
          {run.progress?.at(-1)?.detail ?? "Starting"}... {run.elapsed_secs ?? 0} s
        </p>
      )}
      {run?.status === "failed" && <p role="alert">{run.failure?.message}</p>}
      {run?.status === "done" && run.result && <Results result={run.result} />}
    </section>
  );
}

export function App() {
  const [tails, setTails] = useState<Tails | null>(null);
  const [inputs, setInputs] = useState<Record<ProviderId, string>>({
    openrouter: "",
    gemini: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [calls, setCalls] = useState<Partial<Record<ProviderId, string>>>({});

  useEffect(() => {
    fetch("/api/settings")
      .then(readSettings)
      .then(setTails)
      .catch((err: Error) => setError(err.message));
  }, []);

  async function onSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const body: Partial<Record<ProviderId, string>> = {};
    for (const { id } of PROVIDERS) {
      if (inputs[id].trim()) body[id] = inputs[id];
    }
    try {
      const res = await fetch("/api/settings/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setTails(await readSettings(res));
      setInputs({ openrouter: "", gemini: "" });
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function onTestCall(id: ProviderId) {
    setCalls((prev) => ({ ...prev, [id]: CALLING }));
    const outcome = await testCall(id);
    setCalls((prev) => ({ ...prev, [id]: outcome }));
  }

  return (
    <main style={{ maxWidth: "64rem", margin: "2rem auto", fontFamily: "sans-serif" }}>
      <h1>Respec</h1>
      <form onSubmit={onSubmit} style={{ maxWidth: "28rem" }}>
        {PROVIDERS.map(({ id, label }) => (
          <p key={id}>
            <label>
              {label}
              <br />
              <input
                type="password"
                autoComplete="off"
                value={inputs[id]}
                onChange={(e) => setInputs({ ...inputs, [id]: e.target.value })}
                style={{ width: "100%" }}
              />
            </label>
            <br />
            <small>
              {tails === null
                ? "loading"
                : tails[id]
                  ? `saved, ends in ${tails[id]}`
                  : "not set"}
            </small>
            <br />
            <button
              type="button"
              disabled={!tails?.[id] || calls[id] === CALLING}
              onClick={() => onTestCall(id)}
            >
              Test call
            </button>{" "}
            {calls[id] && <small role="status">{calls[id]}</small>}
          </p>
        ))}
        <button type="submit">Save</button>
        {error && <p role="alert">{error}</p>}
      </form>
      <TestExtraction providers={PROVIDERS.filter(({ id }) => tails?.[id])} />
    </main>
  );
}
