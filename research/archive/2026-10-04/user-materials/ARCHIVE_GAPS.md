# Archive gaps

This archive contains only material actually supplied or recoverable as raw
files during the 2026-10-04 SENEX session.

The historical ORDER075 documents reference two artifacts that were **not**
present among the supplied files:

- `worker.js.reference_only`
  - historical claimed SHA256:
    `fc70f6b0e148f29dbeb823073feadad1b56f954fff01594eb751f6620cbcef42`
- `coverage_spec.json`
  - historical claimed SHA256:
    `bcfa24b960386cb8ea87e13bbf2881fc723f60a67251ad16adcebad5e1ad90ee`

Google Drive search recovered historical audit documents that reference these
hashes, but did not recover raw bytes that could be independently verified
against the claimed digests.

They are therefore classified:

```text
NOT_PROVIDED
NOT_RECONSTRUCTED
NOT_RUNTIME_AUTHORITY
```

Consequences:

- `order075/SHA256SUMS` is preserved as a historical source artifact;
- it is **not** a claim that this Git archive is a complete ORDER075 recovery
  bundle;
- checksum verification of that historical file is expected to remain
  incomplete for `worker.js.reference_only`;
- missing bytes must not be synthesized from prose, hashes, deployed state, or
  current Cloudflare code.
