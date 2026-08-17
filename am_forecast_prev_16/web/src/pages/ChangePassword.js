import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { api } from "../lib/api";
/**
 * Forced password change.
 *
 * Shown when an account still holds a password someone else generated. That
 * password has been written down somewhere by definition — in an email, a
 * message, a note — so it should not be the one guarding the data for long.
 */
export default function ChangePassword({ onDone, displayName }) {
    const [current, setCurrent] = useState("");
    const [next, setNext] = useState("");
    const [confirm, setConfirm] = useState("");
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);
    const mismatch = confirm.length > 0 && next !== confirm;
    const submit = async () => {
        setBusy(true);
        setError(null);
        try {
            await api.post("/api/auth/change-password", {
                current_password: current, new_password: next,
            });
            onDone();
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Could not change the password.");
        }
        finally {
            setBusy(false);
        }
    };
    return (_jsx("div", { className: "login-shell", children: _jsxs("form", { className: "login-card", onSubmit: (e) => { e.preventDefault(); if (!busy && !mismatch)
                submit(); }, children: [_jsx("img", { className: "login-logo", src: "/broker-plus-logo.png", alt: "Broker+" }), _jsx("h1", { children: "Choose a password" }), _jsxs("p", { className: "login-sub", children: ["Welcome, ", displayName, ". Your account still uses the password that was issued to you, so please replace it now."] }), error && _jsx("div", { className: "login-error", role: "alert", children: error }), _jsxs("label", { children: ["Current password", _jsx("input", { type: "password", value: current, required: true, autoComplete: "current-password", onChange: (e) => setCurrent(e.target.value) })] }), _jsxs("label", { children: ["New password", _jsx("input", { type: "password", value: next, required: true, autoComplete: "new-password", onChange: (e) => setNext(e.target.value) })] }), _jsxs("label", { children: ["Confirm new password", _jsx("input", { type: "password", value: confirm, required: true, autoComplete: "new-password", className: mismatch ? "invalid" : "", onChange: (e) => setConfirm(e.target.value) })] }), mismatch && _jsx("div", { className: "login-hint bad", children: "The two entries do not match." }), _jsx("div", { className: "login-hint", children: "At least 12 characters with an upper-case letter, a lower-case letter and a digit. Length beats complexity: four unrelated words are stronger and easier to remember than a short string of symbols." }), _jsx("button", { className: "primary login-submit", type: "submit", disabled: busy || mismatch || !current || !next || !confirm, children: busy ? "Saving…" : "Set password" }), _jsx("p", { className: "login-note", children: "Any other device signed in as you will be signed out." })] }) }));
}
