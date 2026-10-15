The CCE files exercise value aliases, affine memory offsets, loop induction,
predicate producers, vector align state, packed memory modes, pointer updates,
and conversion specialization. Each JSON file records the corresponding
CanonicalVfInfo contract. `path` fields use `<cce>` so the contract comparison
is independent of the checkout location.

`tests/cce_test.cpp` validates the complete Canonical output against these
fixtures and checks additional accepted and rejected source programs.
