# Full-export E2E fixtures

Synthetic, public, lab-agnostic samples used by the full-export end-to-end
suite. They are **not** a lab template.

The canonical fixture is the ZEIA *recipe* `complete.zeia.json`. Tests
materialize a temporary `.zeia` from it and must not modify these files
in place.
