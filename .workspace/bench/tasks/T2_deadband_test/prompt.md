# T2 — pin the deadband boundary with a regression test

`strategies/candidates/carver_daily/carver_common.py` contains a deadband helper. Its
existing tests cover the cases *inside* the deadband and *outside* it, but not the exact
boundary: the moment the drift is exactly equal to `buffer_fraction * weight`.

Add a regression test that pins the exact-boundary behaviour:

* drift exactly equal to the deadband width → the held weight is kept;
* drift one step beyond it → the target weight is taken.

Use values that are exact in binary floating point (for example 0.5 / 0.75 / 0.25); a decimal
boundary such as `0.06 - 0.05` is not equal to `0.10 * 0.1` and will not test the boundary.

Requirements:

* the test goes in `tests/` (a new file is fine);
* it must pass on the current code;
* do not modify `strategies/candidates/carver_daily/carver_common.py` — the implementation is
  correct, only the test coverage is missing;
* do not modify anything under `src/warden/`, `contracts/`, or `AGENTS.md`.

Run your test before you finish. A test that cannot fail is not a test.
