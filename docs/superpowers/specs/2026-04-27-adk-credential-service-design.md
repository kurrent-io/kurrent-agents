# ADK Credential Service — design

**Linear:** [DEV-1481](https://linear.app/kurrent/issue/DEV-1481/implement-kurrentdbcredentialservice-for-adk).

**Status:** draft for review.

**Date:** 2026-04-27.

## Goal

Implement `KurrentDBCredentialService` (currently a stub raising `NotImplementedError`) so ADK samples that use OAuth-bound tools can persist refreshed tokens. Land the service alongside a pluggable cipher abstraction with a working AES-256-GCM default, so credentials are never written to KurrentDB streams in plaintext under the recommended configuration.

DEV-1481 puts encryption out of scope for v1 and earmarks a `CredentialCipher` hook for later. This design promotes that hook to v1: KurrentDB stream-read permission alone should not yield usable credentials, and storage-layer encryption (TDE / disk-level) does not defend against that threat. Encryption is therefore part of the service surface from day one.

## Non-goals

- KMS / envelope encryption with per-row data keys. The cipher protocol is shaped so a `KmsCredentialCipher` can be added later without service changes; we don't ship one.
- Encrypting `credential_key`, the stable hash ADK derives from auth scheme + scopes. It contains no secret material and the load path needs to scan streams by it.
- A canonical cross-framework `CredentialSaved` event. ADK is the only integration with a credential service today; AFW and Strands have no equivalent. The event stays ADK-specific (`extensions.adk` ownership; `events.py` registration).
- Credential redaction in `Capacitor` / observability tooling. Out of scope for the service.
- Migration tooling for existing plaintext data. The library is unshipped (DESIGN.md §6 v1→v2 rename note); there is no production credential data.

## Architecture

### Component layout

New types in `google-adk/python/kurrent_google_adk/`:

```
credential_service.py          # KurrentDBCredentialService (was: stub)
credential_cipher.py           # NEW: CredentialCipher protocol,
                               #      AesGcmCredentialCipher,
                               #      NullCredentialCipher
events.py                      # NEW addition: CredentialSaved
```

- `CredentialCipher` is a `typing.Protocol` over two methods (`encrypt`, `decrypt`) that take and return `bytes` plus a context object holding the AAD inputs. No subclassing required for KMS-backed implementations later.
- `AesGcmCredentialCipher` is the recommended default. Lazy-imports `cryptography`; raises an actionable `ImportError` ("install with `kurrent-google-adk[crypto]`") when the dep is missing.
- `NullCredentialCipher` is the explicit no-encryption path for tests and the small set of users who genuinely don't want encryption. Same on-stream framing as the AES path so readers branch on a single version byte (see Wire format).
- `CredentialSaved` is an ADK-specific Pydantic event registered in `_serialization.py` next to `AgentTransferred`, `Rewind`, `Compaction`, `StateDelta`.

### Cipher protocol

```python
@dataclass(frozen=True, slots=True)
class CredentialContext:
    """Authenticated context bound into the ciphertext via AAD."""
    app_name: str
    user_id: str
    credential_key: str

class CredentialCipher(Protocol):
    def encrypt(self, plaintext: bytes, context: CredentialContext) -> bytes: ...
    def decrypt(self, ciphertext: bytes, context: CredentialContext) -> bytes: ...
```

The plaintext is the JSON serialisation of `AuthCredential.model_dump(mode="json")` encoded as UTF-8. The cipher owns its wire format (including the version byte), so the service treats the ciphertext as opaque bytes.

### Wire format (AES-256-GCM, version `0x01`)

```
| 1B version | 1B key_id | 12B nonce | N bytes ciphertext | 16B tag |
```

- `version=0x01` ⇒ AES-256-GCM with AAD = `f"{app_name}|{user_id}|{credential_key}".encode("utf-8")`.
- `key_id` is a fast-path index into the cipher's key list. `encrypt`
  always uses `keys[0]` and writes `key_id=0`. On `decrypt`, the cipher
  tries the keyed-by-id position first; on `InvalidTag`, it falls back
  to trial decryption against the other keys in the list. The fallback
  is what makes prepend-style rotation work — old ciphertexts tagged
  with `key_id=0` still decrypt after a new key has been prepended,
  because trial-decryption tries the now-shifted old key. A `key_id`
  greater than `len(keys) - 1` raises `UnknownKeyIdError` (forged or
  truncated key list).
- 1 byte ⇒ 256 keys, ample for rotation.
- `nonce` is 12 random bytes per encryption (NIST SP 800-38D §8.2.1 random construction; with `2^32` writes per key the collision probability stays under `2^-32`, comfortably bounded for credential workloads).
- AAD binding: a ciphertext blob lifted from one user's stream and replanted in another user's stream fails to decrypt because the AAD bytes don't match.

### Wire format (Null cipher, version `0x00`)

```
| 1B version=0x00 | UTF-8 JSON bytes |
```

Same outer container, no encryption. Used by tests and explicit opt-out. Readers branch on the version byte and never on event-payload shape, so adding a future `version=0x02` (e.g. KMS envelope) is additive.

### `CredentialSaved` event shape

```python
class CredentialSaved(_EventBase):
    credential_key: str           # auth_config.get_credential_key()
    credential: str               # base64(wire bytes); cipher-self-describing
    timestamp: datetime
```

`credential` is base64 because `_EventBase` serialises through JSON. No `cipher` discriminator field on the event — the version byte inside the wire blob is self-describing. Adding new cipher versions does not change the event schema.

### Service implementation

```python
class KurrentDBCredentialService(BaseCredentialService):
    def __init__(
        self,
        client: AsyncKurrentDBClient,
        *,
        cipher: CredentialCipher,
    ) -> None:
        self._client = client
        self._cipher = cipher

    async def save_credential(
        self, auth_config: AuthConfig, callback_context: CallbackContext
    ) -> None:
        app_name, user_id = self._scope(callback_context)
        key = auth_config.get_credential_key()
        ctx = CredentialContext(app_name, user_id, key)
        plaintext = json.dumps(
            auth_config.exchanged_auth_credential.model_dump(mode="json"),
            separators=(",", ":"),
        ).encode("utf-8")
        wire = self._cipher.encrypt(plaintext, ctx)
        event = CredentialSaved(
            credential_key=key,
            credential=base64.b64encode(wire).decode("ascii"),
            timestamp=datetime.now(timezone.utc),
        )
        stream = for_credentials(app_name, user_id)
        await self._client.append_to_stream(
            stream,
            events=[serialize(event)],
            current_version=StreamState.ANY,
        )

    async def load_credential(
        self, auth_config: AuthConfig, callback_context: CallbackContext
    ) -> AuthCredential | None:
        app_name, user_id = self._scope(callback_context)
        key = auth_config.get_credential_key()
        ctx = CredentialContext(app_name, user_id, key)
        stream = for_credentials(app_name, user_id)
        try:
            recorded = self._client.read_stream(stream, backwards=True)
        except NotFound:
            return None
        async for record in recorded:
            event = deserialize(record)
            if not isinstance(event, CredentialSaved) or event.credential_key != key:
                continue
            wire = base64.b64decode(event.credential)
            plaintext = self._cipher.decrypt(wire, ctx)
            return AuthCredential.model_validate_json(plaintext)
        return None
```

- `StreamState.ANY` matches the rest of the package's non-session services (memory, artifact). The Credentials stream is single-writer in the common case (one ADK process per `(app_name, user_id)`), and the load-latest-by-key semantics tolerate the rare double-write naturally — both `CredentialSaved` events stay in the stream, the most recent wins. If a contention case ever surfaces, the service can move to optimistic concurrency with the per-stream revision-tracking pattern from `KurrentDBSessionService` (DESIGN.md §8) without changing the on-stream shape.
- `_scope` extracts `(app_name, user_id)` from `callback_context`. Both are required; the service is not constructable without them (matches `BaseCredentialService` contract).
- Backward scan + first-match-wins is what DEV-1481 specifies. Not bounded today; see Open questions §1.

### Key rotation

```python
cipher = AesGcmCredentialCipher(keys=[new_key, old_key])
```

- Encrypt always uses `keys[0]` ⇒ new writes encrypt under the new key
  and tag the wire with `key_id=0`.
- Decrypt picks `keys[key_id]` as a fast path, then falls back to trial
  decryption on `InvalidTag` ⇒ old ciphertexts that were tagged with
  `key_id=0` while their key was at index 0 still decrypt after
  prepending, because the fallback finds the now-shifted old key.
- Rotation procedure for a deployment:
  1. Add new key at index 0, keep old at index 1. Deploy. New writes
     now encrypt under the new key; old ciphertexts continue to
     decrypt via the fallback.
  2. Optional: read every existing `Credentials-` stream, decrypt +
     re-encrypt under the new key, append fresh `CredentialSaved`
     events. The append-only model + load-latest semantics retire old
     ciphertexts naturally.
  3. Once confident no old `key_id` is in flight, drop the old key
     from the list. Anything still encrypted under the dropped key
     raises `InvalidTag` (or `UnknownKeyIdError` if its `key_id` is now
     out of range).

Rotation never requires a stream rewrite; it leans on trial-decryption
during the transition and the load-latest semantics of the service to
retire old ciphertexts.

### Packaging

`cryptography` becomes an optional extra:

```toml
[project.optional-dependencies]
crypto = ["cryptography>=42"]
```

`AesGcmCredentialCipher.__init__` lazy-imports `cryptography.hazmat.primitives.ciphers.aead.AESGCM`. On `ImportError` it raises a clear message:

> `AesGcmCredentialCipher requires the cryptography package. Install with: pip install kurrent-google-adk[crypto]`

Users running only `KurrentDBSessionService` / `KurrentDBMemoryService` / `KurrentDBArtifactService` don't pay the native-code wheel cost.

## Error handling

| Failure | Behaviour |
|---|---|
| Wire blob shorter than `version + key_id + nonce + tag` (any cipher) | `MalformedCiphertextError` — corrupt or truncated; surfaced from `decrypt`. |
| `version` byte unknown to the cipher | `UnsupportedCipherVersionError` — e.g. v1 service reading a v2-written blob. |
| `key_id` not in cipher's key list | `UnknownKeyIdError` — caller likely dropped a still-needed key. |
| GCM tag verification fails | `InvalidCiphertextError` — tampering, wrong key, wrong AAD (e.g. blob moved between users). |
| `cryptography` not installed | `ImportError` with install hint, raised at cipher construction. |
| `cryptography` available but rejects key length | `ValueError` from `AESGCM(key)`; we don't catch — let it surface. |

`load_credential` does **not** swallow decrypt errors. A failed decrypt is a real anomaly (key rotation gone wrong, manual stream tampering, blob corruption) and silent fallback to "credential not found" would let auth flows transparently re-prompt for OAuth, hiding the underlying issue. Errors propagate; callers can catch if they have a recovery story.

`deserialize` failures on non-credential events in the stream are still soft-failed per the `_serialization.py` hardening (warn + skip), matching the existing pattern.

## Testing strategy

Live KurrentDB via testcontainers, matching DEV-1481 acceptance:

1. **Round-trip (Null cipher).** save → load returns the same `AuthCredential`. Sanity test for the wire framing without `cryptography` installed.
2. **Round-trip (AES-GCM).** save → load with a 32-byte key. Asserts decrypt succeeds and the `credential` field on the persisted event is *not* the JSON form (no leakage).
3. **Most-recent wins.** Two saves for the same key; load returns the second.
4. **Independent keys.** Saves for different `credential_key` values don't shadow each other.
5. **Missing stream.** load on a never-written `(app_name, user_id)` returns `None`.
6. **Missing key in stream.** load on a key never written to an existing stream returns `None`.
7. **AAD binding rejects relocation.** Manually copy a `CredentialSaved` event from user A's stream to user B's stream; load on user B raises `InvalidCiphertextError`. Defends the AAD design choice.
8. **Key rotation.** Save under key A, add key B at index 0, save again, both loads succeed; drop key A, load of pre-rotation ciphertext raises `UnknownKeyIdError`. Documents the rotation contract.
9. **Wire-format unit tests** (no KurrentDB needed). Roundtrip random plaintext through `AesGcmCredentialCipher` and `NullCredentialCipher`; assert version byte, key_id byte, length invariants.
10. **OAuth sample.** Update `samples/basic_agent` (or a new sample) to exercise an OAuth-bound tool end-to-end and assert the credential lands as a `CredentialSaved` event in KurrentDB. Required by DEV-1481 acceptance.

## Documentation

- Update `google-adk/python/DESIGN.md §7.4`: change "stub" → "done"; replace the encryption-deferred paragraph with a pointer to this design and a wire-format reference. Document the cipher constructor parameter and the `[crypto]` extra.
- Update `google-adk/python/DESIGN.md §13`: close open question 2 (credential encryption — "add v1 as no-op default, or defer to v2?"), pointing at this design.
- Update `schema/SCHEMA_v2.md §5`: add a short subsection or a row noting `CredentialSaved` is ADK-specific and that its `credential` field is a versioned, base64-encoded ciphertext blob whose format is defined in this design.
- Update `kurrent_google_adk/README.md` (or the package-level docstring) with a wiring snippet that includes the cipher.

## Open questions

1. **Bounded backward scan.** A long-lived agent could refresh OAuth tokens many times. `load_credential` scans backward without bound; ADK's stock `InMemoryCredentialService` is a dict, so there is no upstream precedent for a limit. Initial position: unbounded, document the cost characteristic, revisit if a workload surfaces an issue. Could later add an opt-in projection or a `credential_key`-keyed catch-up read.
2. **Sample choice.** DEV-1481 says "update `samples/basic_agent` (or a new sample) to exercise an OAuth-bound tool". OAuth flow needs a real provider. Open: pick a Google OAuth flow that ADK already wires up, or stub a fake `OAuth2` `AuthScheme` against a local mock to keep tests hermetic. Mock is friendlier for CI; real OAuth is more honest. Lean: hermetic mock for the integration test, real OAuth for the live sample.
3. **Cipher construction friction.** Forcing every `KurrentDBCredentialService` user to construct a cipher is the explicit goal (no plaintext default), but the friction is real for someone who doesn't yet know about credentials. Options: (a) keep the constructor required, document `NullCredentialCipher` for the no-thanks path, (b) class-method helper `KurrentDBCredentialService.with_aes_gcm(client, key=...)` that builds the default cipher inline. Lean: (a) for explicitness; (b) is a cheap follow-up if users push back.
4. **Key sourcing convention.** Should `AesGcmCredentialCipher` ship a `from_env(name="KURRENT_ADK_CREDENTIAL_KEYS")` constructor that parses a comma-separated list of base64-encoded keys? Convenient for 12-factor deployments. Lean: yes, it's eight lines and the alternative is every user writing the same parser.
