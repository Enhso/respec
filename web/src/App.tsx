import { type SubmitEvent, useEffect, useState } from "react";

type Tails = { openrouter: string | null; gemini: string | null };

const PROVIDERS = [
  { id: "openrouter", label: "OpenRouter key" },
  { id: "gemini", label: "Google Gemini key" },
] as const;

type ProviderId = (typeof PROVIDERS)[number]["id"];

type TestCallResult =
  | { ok: true; model: string; reply: string }
  | { ok: false; message: string };

const CALLING = "calling...";

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
    <main style={{ maxWidth: "28rem", margin: "2rem auto", fontFamily: "sans-serif" }}>
      <h1>Respec</h1>
      <form onSubmit={onSubmit}>
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
    </main>
  );
}
