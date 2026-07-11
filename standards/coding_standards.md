# Coding Standards (KB standards doc for the Reviewer)

These are the standards the Reviewer checks a diff against. They are a
versioned KB artifact — amended by retrospectives (Phase 6), not ad hoc.

## Correctness
- Code must satisfy every acceptance criterion on the ticket.
- No dead code, no commented-out blocks, no debug prints left in.
- Handle the error/edge cases named in the acceptance criteria explicitly.

## Security
- Never hardcode secrets, credentials, tokens, or private keys. Read them
  from the environment or a secrets manager.
- Validate and sanitize all external input before use.
- Do not log secrets or full request bodies.
- Prefer parameterized queries; never build SQL/shell strings from input.

## Errors
- Raise or return explicit errors; never silently swallow exceptions.
- Fail closed on security-relevant decisions.

## Tests
- Every code file with logic has tests that assert behavior, not just that it
  runs.
- Tests are deterministic: no real network, no clocks, no randomness without
  a fixed seed.
- Cover the unhappy paths (invalid input, boundaries), not only the happy path.

## Style & structure
- Small, single-responsibility functions; names say what they do/return.
- No absolute paths; repo-relative only. Packages have `__init__.py`.
- Match the conventions already present in the surrounding code.
- No new third-party dependency unless it is necessary and justified.
