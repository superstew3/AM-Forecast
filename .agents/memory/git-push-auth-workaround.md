---
name: git push auth workaround in this Replit environment
description: Plain `git push` to GitHub over HTTPS fails 401 here even with a valid token; use an explicit Authorization header and bypass the global git config.
---

Plain `git push https://x-access-token:<TOKEN>@github.com/...` fails with GitHub's generic
`401 Invalid username or token` in this workspace, even when the token is confirmed valid and
has push access (verified separately via `curl` to the same git smart-HTTP endpoint, which
succeeds). The workspace's system/global git config (`GIT_CONFIG_GLOBAL`, `GIT_ASKPASS`) is
intercepting/rewriting the URL-embedded credential before git sends it.

**Working fix:** bypass that config and set the Authorization header explicitly:

```sh
GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
git -c http.extraHeader="Authorization: Basic $(printf 'x-access-token:%s' "$GITHUB_PUSH_TOKEN" | base64 -w0)" \
push "https://github.com/<owner>/<repo>.git" main:main
```

**Why:** confirmed by direct comparison — identical token, identical URL scheme, curl succeeds,
plain `git push` fails; only difference is git's own credential path through the global config.

**How to apply:** whenever a task or user preference requires pushing to GitHub from this
environment (e.g. "auto-commit and push after every change"), use this pattern instead of a bare
`git push`. Store the PAT as a secret (e.g. `GITHUB_PUSH_TOKEN`) via `requestSecrets` — never
paste tokens into shell commands or chat literally. If push fails with 401, the secret's stored
token may be stale/wrong (e.g. accidentally holds a password instead of a PAT) — re-request it
rather than re-debugging git config.

**Side effect:** pushing straight to a URL like this (rather than to the configured `origin`
remote) does NOT update the local `refs/remotes/origin/<branch>` tracking ref. Commands that read
that cached ref (`git log origin/main`, `git ls-tree origin/main`) can show stale state even
though the push succeeded and GitHub is current. Run `git fetch origin <branch>` (with the same
header/env bypass) after pushing if you need local commands to reflect the live remote, or check
liveness directly with `git ls-remote origin <branch>`.
