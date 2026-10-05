# Changelog

`hellohq-plugin-sdk` (Python). Versions follow semver; a new host call is an
additive minor.

## 0.2.0

### Added
- `host.propose(batch)`: the Tier 1 propose-only write call
  (`propose` / `propose_response`, `plugin-protocol` `sidecar/host-calls.schema.json`).
  Returns one typed `Receipt(index, outcome, reason)` per proposal. Needs
  `propose:holdings` and/or `propose:valuations` (Verified tier).
- `hellohq_plugin_sdk.proposals`: `Holding`, `Valuation`, `ProposalBatch`,
  `Money`, `Quantity`, `Instrument`, `Source`; `Outcome`; decimal amounts go on
  the wire as canonical strings and `float` is refused.
- Typed refusals, all `PluginError` subclasses: `ProposePermissionDenied`,
  `ProposeUnsupported`, `ProposeRateLimited`, `ProposeQuotaExceeded`,
  `ProposeTooLarge`, `ProposeTooMany`, `ProposeBadRequest`,
  `ProposeWorkspaceUnavailable`, `ProposeHostError` (base `ProposeError`).
- `hellohq_plugin_sdk.proposal_validation`: a client-side pre-check that mirrors
  the host's field rules and closed reason codes. Convenience only: the host
  validates every batch itself.
- Tests that validate every request the SDK emits and every reply it parses
  against `plugin-protocol`'s `host-calls.schema.json` (set
  `HELLOHQ_PLUGIN_PROTOCOL_DIR`, or keep a sibling `plugin-protocol` checkout).

### Changed
- Internal: `host._rpc` is split into `host._exchange` (send and read one reply)
  and `_rpc` (which also raises on an `error`). No behaviour change for
  `ai_complete`, `storage_*` or `fetch`.

`PROTOCOL_VERSION` is unchanged (`0.1.0`).

## 0.1.0

Initial release: `serve`, `emit_event`, `host.ai_complete`, `host.storage_*`,
`host.fetch`.
