# ADR 0002 — Plugin signing & verification: hybrid EC-P256 + ML-DSA-65

- Status: Accepted
- Date: 2026-06-28
- Scope: how HelloHQ plugins (WASM components published via the plugin-sdk CLI)
  are signed at publish time and verified by hosts before loading.

> Numbering note: this is `0002` even though `0001` may not yet be on `main` —
> it lands when the streaming-toolchain branch merges.

## Context

Plugins are WASM components published via the CLI into the registry; hosts load
them with wasmtime. A host must confirm a plugin is **authentic** (published by
HelloHQ) and **untampered** before loading it — including that its declared
capabilities/permissions haven't been altered.

Two constraints shape the scheme:

- **Post-quantum requirement.** Signatures must be verifiable with the
  `mldsa-verify` library (ML-DSA-65, FIPS 204).
- **Verification runs on untrusted devices** (mobile, desktop). Verifiers must be
  pure functions over public data — no secret material on the verify side.

## Decision

1. **Hybrid signatures.** Every release is signed with **both**:
   - **ECDSA P-256** (classical), and
   - **ML-DSA-65** (FIPS 204 — *pure* ML-DSA, empty context) (post-quantum).

   A plugin is trusted only if **both** verify. This hedges the PQC transition: a
   break in either primitive alone does not forge a plugin.

2. **What is signed — the manifest digest.** A canonical digest over the
   component bytes **and** its metadata (name, version, declared
   capabilities/permissions). Signing the manifest (not just the `.wasm`) prevents
   re-pointing the artifact or tampering with its declared permissions.

3. **Signature envelope.** Detached, published alongside the artifact:
   - the ECDSA P-256 signature,
   - the raw FIPS-204 ML-DSA-65 signature (3309 bytes),
   - the key identifiers and the manifest digest.

   The envelope is vendor-neutral and carries no key-management details.

4. **Key custody.** Signing private keys are held in a **FIPS 140-3 Level 3 HSM**
   and are **non-exportable**; signing happens via the HSM's signing API inside CI,
   which authenticates via OIDC/Workload Identity (no key material in the
   pipeline). Operational custody specifics are deliberately out of scope of this
   public spec.

5. **Verification (host).**
   - ML-DSA-65 via **`mldsa-verify`** — public key 1952 bytes, signature 3309
     bytes, pure ML-DSA with empty context.
   - ECDSA P-256 via the platform's standard crypto.
   - Both must pass over the **same** canonical manifest digest, else the plugin is
     rejected. **Fail closed.**

6. **Trust distribution, rotation, revocation.** Hosts ship a pinned trust store of
   current signing public keys (anchored by an offline root). Multiple public keys
   may be valid at once to bridge rotation; a published revocation list lets hosts
   reject revoked keys. Public keys are served from a HelloHQ endpoint over TLS.

## Consequences

- Larger signatures (EC ~64–72 B + ML-DSA-65 3309 B) and a double verify —
  negligible for plugin-sized artifacts.
- Every host language needs **both** verifiers: `mldsa-verify` (C ABI; FFI for
  Dart/Swift/Kotlin) for the PQC half, platform crypto for ECDSA. A JS/web ML-DSA
  path (a WASM build of `mldsa-verify`) must be provided.
- The manifest needs a **stable, versioned canonical serialization** so signer and
  verifier hash identical bytes.
- Rotation + revocation must be operational before the first signed release.

## Alternatives considered

- **ML-DSA only** — simpler, but no classical fallback if ML-DSA is broken.
  Rejected for a root of trust.
- **Classical only** (Ed25519 / ECDSA) — fails the post-quantum requirement.
- **Sign the artifact bytes only** (not the manifest) — would leave
  metadata/permissions tamperable. Rejected.
