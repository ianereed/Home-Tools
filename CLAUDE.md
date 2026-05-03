# gstack tools by default

Use the `gstack` skills/tools by default for any task they cover —
browsing, QA, design review, ship/land/deploy, codex, scrape,
investigate, etc. Reach for the gstack equivalent before a
generic Bash/MCP path. Specifically:

- Web browsing → `/browse` (never `mcp__claude-in-chrome__*`).
- QA / dogfooding a site → `/qa` or `/qa-only`.
- Visual / design polish on a live site → `/design-review`.
- Plan reviews → `/plan-eng-review`, `/plan-design-review`,
  `/plan-ceo-review`, `/plan-devex-review`, or `/autoplan`.
- Shipping → `/ship`, then `/land-and-deploy`.
- Second opinion on code → `/codex`.
- Debugging reported errors → `/investigate` (don't debug directly).
- Pulling data from a page → `/scrape`.

If unsure whether a gstack skill covers the task, check the
available-skills list before falling back to a generic tool.

# Journal

At the start of a session, create a new journal file at the root of
the working directory: `journal-N.md`, where N is one higher than
the highest existing `journal-*.md` (start at 1 if none exist). A
SessionStart hook may have already created it — if so, append to
that file rather than creating another.

Append an entry for every non-trivial action you take. Write it as
you do the work, not as a summary at the end.

Each entry should include:
- ISO timestamp (`YYYY-MM-DD HH:MM`)
- One-line summary
- The exact command, if one was run, and the verbatim stdout/stderr
- Files edited and why
- Hypotheses and whether they held up
- Dead-ends, with a note on why the thing didn't work
- Links read during research
- Decisions made and the reasoning behind them

## Verbatim output, not summaries

Vague entries are worse than none. "Ran the command, it worked" or
"tests passed" is useless to future-you. Paste the actual command
and the actual output, even if long. If output is genuinely huge,
capture the salient lines verbatim and note where the rest came
from. The journal exists so a future agent can reconstruct what
happened — paraphrases break that.

## Reading the journal

Before starting new work, or after a context compaction, read the
current journal to orient yourself. If this is a fresh attempt at a
task you've tried before, skim the previous `journal-*.md` files
too.

Long journals cost tokens. If the file is large, read just the
recent entries (Read with `offset`, or `tail -n` via Bash) instead
of the whole file. Only widen if recent entries reference older
context.

## Secrets and committing

Journals capture raw command output — which often includes API
keys, tokens, internal hostnames, local paths, and other material
that should not land in git. Never `git add journal-*.md` without
scanning the file first. Add `journal-*.md` to `.gitignore` as the
default; propose it if the project does not already have it.
