# Why no frontend change has taken effect

## What is happening

`web/src/` contains **26 compiled `.js` files committed alongside their `.tsx`
sources**, shadowing 24 of them:

```
src/pages/Bonus.js     18 Aug   <- stale build output, committed
src/pages/Bonus.tsx    19 Aug   <- the source we edit
src/components/ui.js   18 Aug
src/components/ui.tsx  19 Aug
src/lib/api.js         18 Aug
src/lib/api.ts         18 Aug
```

Vite resolves `.js` before `.tsx`, so the browser is served yesterday's build.
Editing the `.tsx` changes nothing that anyone can see.

This is why the seven-metric block and `$13.00` are still on screen while the
bonus explainer -- which is backend -- updated correctly in the same deploy. One
half of the change landed and the other could not.

It also means **any earlier frontend work is in the same position**: the sources
may already contain fixes nobody has ever seen rendered.

## The fix

Delete every `.js` that shadows a `.tsx` or `.ts`, and stop them coming back.

```bash
cd am_forecast/web/src
find . -name "*.tsx" -not -path "*/node_modules/*" | while read f; do
  rm -f "${f%.tsx}.js"
done
rm -f lib/api.js __tests__/format.test.js
```

That removes 26 files. None is a source: each is generated from the `.tsx` or
`.ts` beside it, and `npm run build` recreates them into `dist/` where they
belong.

Then add to `.gitignore` so they cannot be committed again:

```
web/src/**/*.js
web/dist/
```

## Verify before committing

```bash
cd am_forecast/web
npm run build
```

The build must succeed with no `.js` files in `src/`. If it fails with a missing
module, one of the deleted files was genuinely a source rather than build output
-- report which, do not restore blindly.

Then reload the Bonus page. You should see **three metric cards** rather than
seven, **"What is payable"** as the panel title, the GST note beneath it, and
**"13 managers in the scheme"** as plain text in the footnote rather than
`$13.00`.

## Why this matters beyond today

Committed build output that shadows source is a silent trap: the code says one
thing, the running app does another, and nothing errors. It cost this change a
full deploy cycle to find, and it would have cost every frontend change after it
the same.
