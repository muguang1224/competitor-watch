---
name: competitor-watch
description: Monitor public sources (GitHub releases, RSS/Atom feeds, changelog and pricing pages, public JSON APIs) for changes, diff them against the last snapshot, and produce a plain-language technical brief of what a competitor actually shipped. Use when the user asks to track competitors, watch a changelog or release feed, get a "what changed" digest, or periodically summarize feature updates from public web sources. Only ever reads public, no-login pages — never authenticated or private interfaces.
---

# competitor-watch

Turn "what has competitor X done lately?" into a repeatable, diffable job. You point it at the **public** pages a company already publishes for humans — release feeds, changelogs, docs, pricing tables, status pages — and it snapshots them, detects what changed since last run, and hands you the raw material for a feature brief.

It does the boring, deterministic part (fetch, snapshot, diff, cite). You write the brief.

## When to use this skill

- "Track these 5 competitors and tell me what shipped this week."
- "Watch the changelog of X and ping me when pricing changes."
- "Give me a feature-diff brief on Y vs. last month."
- Any recurring "what's new in <project/company>" request.

Do **not** use it for anything that requires logging in, an API key, or scraping a mobile app's private backend. See *Guardrails*.

## How it works

1. **Describe sources** in a JSON config (see `examples/sources.example.json`).
2. **Fetch + snapshot** each source with `scripts/watch.py`. First run records a baseline; later runs diff against it.
3. **Read the report** the script writes (markdown). New/changed items are called out with links and dates.
4. **You synthesize** the brief: group the raw changes into themes, decide what actually matters, write the 3-sentence "so what".

The script is deliberately dumb and honest: it never invents a change it didn't see, and it always carries the source URL + timestamp so the brief is auditable.

## Running it

```bash
# first run — creates the baseline, report shows everything as "new"
python3 scripts/watch.py --config examples/sources.example.json --state .cw-state --out brief.md

# later runs — only shows what changed since the snapshot
python3 scripts/watch.py --config my-sources.json --state .cw-state --out brief.md
```

Zero third-party dependencies — standard library only (urllib, xml, difflib). Runs anywhere Python 3.8+ does.

### Source types

| `type`            | Fields                          | What it watches |
|-------------------|---------------------------------|-----------------|
| `github_releases` | `repo` (e.g. `owner/name`)      | Release/tag feed via the public GitHub REST API |
| `rss`             | `url`                           | Any RSS 2.0 or Atom feed |
| `page`            | `url`                           | A changelog / pricing / docs page — hashes the visible text and shows a line diff |
| `json`            | `url`, optional `label`         | A public, no-auth JSON endpoint — shows a key-level diff |

Every source entry takes an optional `"name"` used in the report, and an optional `"notes"` string for your own context.

### Output

`brief.md` contains, per source: a status line (`NEW ITEMS`, `CHANGED`, `UNCHANGED`, or `ERROR`), the new items with title/date/URL, and for page/JSON sources a unified diff of what actually changed. The raw snapshot lives in `--state` as JSON so runs are reproducible and diffable in git if you want.

## Writing the brief (the part that matters)

After the script runs, don't dump the diff. Do this:

1. **Triage** — mark each change as feature / pricing / docs-only / noise.
2. **Cluster** — roll related items under one heading ("they shipped an agent runtime").
3. **So what** — one line per cluster on why it matters to *your* product, not theirs.
4. **Cite** — link the source item for every claim. If the script didn't see it, don't claim it.

See `references/brief-template.md` for a fill-in structure.

## Guardrails

- **Public only.** Everything this skill touches must be reachable without a login and without an API key. If a page needs auth, a session cookie, or you'd have to reverse-engineer an app's backend to get it — stop. That's out of scope and often against terms of service.
- **Respect robots/rate limits.** Default per-request delay is applied; keep source lists small and poll no more than daily.
- **No private or gated data.** No financial accounts, no logged-in dashboards, no "just find a way in".
- **Attribute.** Every reported change carries its URL. The brief is only as good as its citations.

## Files

- `scripts/watch.py` — fetch / snapshot / diff engine (stdlib only)
- `examples/sources.example.json` — a ready-to-edit config
- `references/brief-template.md` — the brief structure to fill in
