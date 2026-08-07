import { useState } from "react";
import { api } from "../lib/api";

/**
 * Forced password change.
 *
 * Shown when an account still holds a password someone else generated. That
 * password has been written down somewhere by definition — in an email, a
 * message, a note — so it should not be the one guarding the data for long.
 */
export default function ChangePassword({ onDone, displayName }: {
  onDone: () => void; displayName: string;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mismatch = confirm.length > 0 && next !== confirm;

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      await api.post("/api/auth/change-password", {
        current_password: current, new_password: next,
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change the password.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-shell">
      <form className="login-card"
            onSubmit={(e) => { e.preventDefault(); if (!busy && !mismatch) submit(); }}>
        <img className="login-logo" src="/broker-plus-logo.png" alt="Broker+" />
        <h1>Choose a password</h1>
        <p className="login-sub">
          Welcome, {displayName}. Your account still uses the password that was
          issued to you, so please replace it now.
        </p>

        {error && <div className="login-error" role="alert">{error}</div>}

        <label>
          Current password
          <input type="password" value={current} required autoComplete="current-password"
                 onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label>
          New password
          <input type="password" value={next} required autoComplete="new-password"
                 onChange={(e) => setNext(e.target.value)} />
        </label>
        <label>
          Confirm new password
          <input type="password" value={confirm} required autoComplete="new-password"
                 className={mismatch ? "invalid" : ""}
                 onChange={(e) => setConfirm(e.target.value)} />
        </label>
        {mismatch && <div className="login-hint bad">The two entries do not match.</div>}

        <div className="login-hint">
          At least 12 characters with an upper-case letter, a lower-case letter
          and a digit. Length beats complexity: four unrelated words are stronger
          and easier to remember than a short string of symbols.
        </div>

        <button className="primary login-submit" type="submit"
                disabled={busy || mismatch || !current || !next || !confirm}>
          {busy ? "Saving…" : "Set password"}
        </button>
        <p className="login-note">
          Any other device signed in as you will be signed out.
        </p>
      </form>
    </div>
  );
}
