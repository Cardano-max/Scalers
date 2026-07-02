# PII egress: memory & tenant data stay in our Postgres (fr1.4 AC-5)

**Invariant.** Customer/lead PII — contact memories, agent/campaign memories,
suppression-ledger phone numbers, consent, send/delivery events — is stored
**only in our own Postgres**. There is **no hosted memory cloud** (no external
vector-DB SaaS, no third-party "memory" API, no LLM-provider memory store) in
the read or write path. This is a privacy / client-ToS gate: a client's contact
data never leaves infrastructure we control.

## What enforces it

- **Storage.** `engine/memory/store.py` (`memories`) and
  `engine/suppression/ledger.py` (`contact_memories`, `suppression_ledger`,
  `consent`, `send_events`, `delivery_events`, `carrier_errors`) persist via
  `psycopg` to the local Postgres only. No HTTP client is imported or used in
  these write/recall paths.
- **Embeddings are local.** The default embedder (`kb.embedding.make_embedder`)
  runs a local ONNX model; `$SCALERS_EMBEDDER` selects a deterministic local
  stub for hermetic runs. Embedding a memory does **not** call a hosted API.
- **Isolation.** Row-Level Security (`18-tenant-isolation.sql` + the memory
  store's own policy) scopes every non-superuser read/write to the session's
  `app.current_tenant`, so even within our Postgres one tenant's PII is not
  readable by another.

## The check

`engine/tests/test_pii_egress.py` asserts the memory/ledger modules import no
hosted-cloud egress client (a source scan for banned markers) and that the
default embedder is local. If a future change introduces an external memory
service, that test fails — the invariant is enforced in CI, not just documented.

## Out of scope

Outbound *connector* calls (Twilio/Gmail/Meta) are a separate, intended egress
for the send itself and are governed by the redirect pins + `check_send_allowed`
gate — they are not a memory/PII store.
