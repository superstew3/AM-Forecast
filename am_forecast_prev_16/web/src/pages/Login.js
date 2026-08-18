import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
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
export default function Login({ onSignedIn }) {
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);
    const [show, setShow] = useState(false);
    const submit = async () => {
        setBusy(true);
        setError(null);
        try {
            const user = await api.login(email.trim(), password);
            onSignedIn(user);
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Sign-in failed.");
            setPassword("");
        }
        finally {
            setBusy(false);
        }
    };
    return (_jsxs("div", { className: "login-shell", children: [_jsxs("form", { className: "login-card", onSubmit: (e) => { e.preventDefault(); if (!busy)
                    submit(); }, children: [_jsx("img", { className: "login-logo", src: "/broker-plus-logo.png", alt: "Broker+" }), _jsx("h1", { children: "Income Forecasting" }), _jsx("p", { className: "login-sub", children: "Performance & budget \u00B7 Broker+" }), error && _jsx("div", { className: "login-error", role: "alert", children: error }), _jsxs("label", { children: ["Email address", _jsx("input", { type: "email", value: email, autoComplete: "username", autoFocus: true, required: true, inputMode: "email", onChange: (e) => setEmail(e.target.value) })] }), _jsxs("label", { children: ["Password", _jsxs("span", { className: "password-field", children: [_jsx("input", { type: show ? "text" : "password", value: password, required: true, autoComplete: "current-password", onChange: (e) => setPassword(e.target.value) }), _jsx("button", { type: "button", className: "reveal", tabIndex: -1, "aria-label": show ? "Hide password" : "Show password", onClick: () => setShow(!show), children: show ? "Hide" : "Show" })] })] }), _jsx("button", { className: "primary login-submit", type: "submit", disabled: busy || !email || !password, children: busy ? "Signing in…" : "Sign in" }), _jsx("p", { className: "login-note", children: "Access is limited to named accounts. Sign-in attempts are recorded, and an account locks for 15 minutes after five failed attempts. If you are locked out or need an account, speak to an administrator." })] }), _jsx("p", { className: "login-footer", children: "All income figures in this system are GST inclusive." })] }));
}
