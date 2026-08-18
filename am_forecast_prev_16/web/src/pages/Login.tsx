import { useState } from "react";
import { api } from "../lib/api";

/**
 * Sign-in.
 *
 * Deliberately plain: one card, no marketing, no distractions. A sign-in screen
 * is a door, not a landing page.
 *
 * The error text comes from the server unchanged, because the server is careful
 * not to reveal whether an address has an account. Helpful client-side messages
 * like "no account with that email" would undo that.
 */
export default function Login({ onSignedIn }: { onSignedIn: (u: any) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [show, setShow] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const user = await api.login(email.trim(), password);
      onSignedIn(user);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign-in failed.");
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-shell">
      <form className="login-card"
            onSubmit={(e) => { e.preventDefault(); if (!busy) submit(); }}>
        <img className="login-logo" src="/broker-plus-logo.png" alt="Broker+" />
        <h1>Income Forecasting</h1>
        <p className="login-sub">Performance &amp; budget &middot; Broker+</p>

        {error && <div className="login-error" role="alert">{error}</div>}

        <label>
          Email address
          <input type="email" value={email} autoComplete="username" autoFocus
                 required inputMode="email"
                 onChange={(e) => setEmail(e.target.value)} />
        </label>

        <label>
          Password
          <span className="password-field">
            <input type={show ? "text" : "password"} value={password} required
                   autoComplete="current-password"
                   onChange={(e) => setPassword(e.target.value)} />
            <button type="button" className="reveal" tabIndex={-1}
                    aria-label={show ? "Hide password" : "Show password"}
                    onClick={() => setShow(!show)}>
              {show ? "Hide" : "Show"}
            </button>
          </span>
        </label>

        <button className="primary login-submit" type="submit"
                disabled={busy || !email || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>

        <p className="login-note">
          Access is limited to named accounts. Sign-in attempts are recorded, and
          an account locks for 15 minutes after five failed attempts. If you are
          locked out or need an account, speak to an administrator.
        </p>
      </form>
      <p className="login-footer">
        All income figures in this system are GST inclusive.
      </p>
    </div>
  );
}
