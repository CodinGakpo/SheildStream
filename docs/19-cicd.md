# Chapter 19 — Shipping It Without Pretending

A CI/CD pipeline is easy to fake and hard to get genuinely right, and the gap
between those two is almost entirely a matter of honesty about what actually
works without external dependencies the environment doesn't have yet. The
pipeline built this week is split deliberately along exactly that line: the
parts that need nothing but the code itself are real, run on every push, and
were checked locally before being trusted in CI at all — and the parts that
need real infrastructure this project doesn't yet have provisioned (Chapter
17) are honestly gated, not faked into appearing to work.

## Lint and test: verified locally first, because a CI config that fails on its own first run isn't a deliverable

Before wiring linting and testing into the pipeline, both were run directly,
by hand, against the actual codebase — and that check caught two real,
pre-existing problems that would otherwise have made the very first CI run
fail on unrelated grounds, undermining trust in the pipeline before it had
run once. One was a genuinely unused import sitting in a test file, left
over from an earlier edit, harmless in isolation but exactly the kind of
thing a linter is supposed to catch and exactly the kind of thing that would
have failed the very first lint run for a reason that had nothing to do with
whatever change actually triggered that run. The other was a local
development environment file that had quietly drifted out of sync with a
configuration field added back in Chapter 13 (`ADMIN_DATABASE_URL`) — present
in the actual application code for months, never backfilled into the local
`.env` file used to run tests outside Docker, so the full test suite hadn't
actually been runnable from a clean local environment in exactly that
configuration until this was caught and fixed here.

Both fixes were small. The point isn't their size — it's that they were
found by actually running the thing before shipping a pipeline that assumed
it would work, rather than writing the CI configuration first and discovering
these gaps only when a real pipeline run failed on something unrelated to
whatever change had actually been pushed.

With both fixed, lint and test are genuinely functional CI jobs, not
aspirational ones: linting runs against the gateway, the consumers, and the
dashboard on every push and pull request; the gateway's test suite
(twenty-three tests) and the consumers' test suite (thirty-five tests) both
run with no service containers required at all, because neither suite ever
touches a live database or a live Redis instance — every test either mocks
its infrastructure dependency directly or uses `fakeredis` (the same
real-Lua-interpreter-backed fake used all the way back in Chapter 7),
reserving genuine integration coverage against the real running stack for the
Playwright suite from Chapter 12 instead. That separation of concerns is
what makes the unit-test CI jobs both fast and dependency-free.

## Build and scan: real, and scoped to avoid alert fatigue on purpose

Docker images are built and pushed to GitHub's own container registry using
the platform's built-in, automatically-scoped token — never a manually
generated, long-lived personal access token stored as a secret. That's a
small detail with an outsized security benefit: a credential that's
automatically scoped to exactly this workflow run, and expires with it,
simply can't leak the way a stored long-lived token can.

Every built image is then scanned with Trivy, configured to fail the build
only on CRITICAL-severity findings — deliberately not MEDIUM or LOW. This
mirrors a decision this project already made once before, for an entirely
different reason: Chapter 11's alert deduplication logic exists specifically
because a flood of low-priority signal drowns out the signal that actually
matters, right when it matters most. The same reasoning applies here. A scan
gate that fails a build on every low-severity finding trains whoever's
watching it to stop reading the output at all, which defeats the entire
purpose of scanning in the first place.

## What's honestly gated, and why

Three jobs — running database migrations, deploying to the actual VM, and a
post-deploy smoke test — are gated behind a repository variable that stays
unset until Chapter 17's deployment runbook has actually been executed once
against real infrastructure. This isn't a placeholder pretending to be
finished; it's an honest acknowledgment that these steps depend on
credentials and a running target this project doesn't currently have, and a
pipeline that attempted to run them anyway would just fail loudly and
uninformatively on every single run until someone provisioned the missing
pieces — noise that trains exactly the same bad habit the Trivy severity
threshold above is designed to avoid.

There's a more specific limitation worth stating plainly rather than
glossing over: this pipeline's stated ambition for the deploy step was zero
stored long-lived credentials, achieved via OpenID Connect federation — the
same pattern that lets the image-build step above use an automatically
scoped, short-lived token instead of a stored one. That ideal genuinely works
against a cloud provider's own identity infrastructure, which is exactly what
the original AWS-native deployment plan (set aside back in Chapter 17) would
have had access to. The hybrid deployment target chosen instead is a bare
virtual machine, with no cloud identity system to federate against at all —
SSH is the only way in, and SSH fundamentally requires *some* stored
credential, no matter how carefully the rest of the pipeline is built around
avoiding exactly that pattern everywhere else it can. The deploy job's stored
SSH key is the one deliberate, acknowledged exception to an otherwise
zero-stored-credential pipeline — not an oversight, a documented trade-off
that follows directly from Chapter 17's own choice of deployment target.

## The pattern, one more time

Every chapter in this book has followed roughly the same shape: state the
problem, consider the alternatives, make a decision, and then actually verify
it rather than trust the reasoning that led there. This chapter is the same
shape applied to the pipeline that ships the code itself — verify the parts
that can be verified without external dependencies, and be honest, in the
configuration itself, about the parts that genuinely can't be yet.
