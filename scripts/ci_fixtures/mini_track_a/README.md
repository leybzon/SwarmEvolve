# mini_track_a — reproducibility fixture (M20)

This directory holds the canned-response template that
`scripts/reproduce.py` feeds into `scripts/tracks/track_a.py` to run
its byte-identical smoke test. The harness duplicates `response.md`
into 40 numbered `.md` files at runtime, so the committed footprint
stays minimal.

Contract:

* `response.md` **must** contain at least one fenced ``` ```cpp ``` block.
* That block must compile against the current engine / ABI and wrap
  its entrypoint in `namespace TeamA { ... }` so evolve can accept it
  as a candidate without further rewriting.
* Both back-to-back runs of `reproduce.py` must produce the same
  fingerprint (see `scripts/reproduce.py::fingerprint_run`).

If you deliberately change the fixture source, update the digest in
whatever CI job pins it (none today; CI currently asserts equality
between the two run_a / run_b fingerprints rather than against a
committed digest). Grep for `mini_track_a` to find downstream sites.
