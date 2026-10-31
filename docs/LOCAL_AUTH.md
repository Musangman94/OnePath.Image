# Local owner authentication interface

Updated: 31 October 2026, Asia/Kuala_Lumpur.

**Status: backend source-wired and independently verified within the scoped auth integration review; browser pairing remains unaccepted.** The current `main.py` includes the local-auth router and default-deny middleware. Both startup modes initialize auth before provider construction/probing, and restart quarantine permits only the named authentication POSTs without clearing its runtime fence. The backend review passed its scoped API, startup/CLI, and temporary-session production-handler SSE checks; it did not start the application server or use owner data. An actual Edge review found that Escape dismissed the locked pairing dialog on the reviewed revision. Frontend changed its guard (`owner-pairing.js` SHA-256 `C420FF7BE8EEA87090711A825C27C0ACBD816EF1C2F78EEBFBBC359B2830A5A3`) and targeted fake checks passed, but the fix has not been verified in a real browser. The required internal browser was unavailable, and no Edge fallback was used. No packaged, native-POSIX, or owner acceptance is claimed. This document does not define remote/LAN client identity, a DevHub grant, or production acceptance.

## Protected surface

All private `/api/v1` reads, mutations, downloads, and event streams must authenticate by default. There is no authentication-off switch. Narrow public exceptions are static frontend content, coarse `/api/v1/health`, coarse `/api/v1/auth/status`, and the validated pairing-code bootstrap endpoint. Each exception returns only what that operation needs; none grants owner access. Keep existing loopback, `Host`/`Origin`, and same-origin mutation checks. Do not enable wildcard CORS.

The existing restart-recovery quarantine has precedence over provider and application-data behavior. Unknown durable work stays read-only. Its blanket POST denial needs a narrow exception for the auth-only endpoints `POST /api/v1/auth/pairing-codes`, `POST /api/v1/auth/pair`, and `POST /api/v1/auth/logout`, so the owner can restore identity and read history. These endpoints may not clear the runtime fence, trigger provider probes/model actions, or permit any application-data mutation while recovery is required.

## Pairing and session flow

1. A trusted owner CLI reads the private bootstrap credential file and sends `Authorization: OnePathBootstrap <secret>` to `POST /api/v1/auth/pairing-codes`. The server verifies the current credential epoch and loopback/request origin. Rate-limit invalid pairing attempts to **10 per 60 seconds**, then return `429`. The trusted CLI receives `{ "contractVersion": 1, "code": "…", "expiresAt": "<ISO-UTC>" }`; the one-use code expires after **300 seconds**. The bootstrap secret itself is never returned.
2. The browser submits only `{ "code": "…" }` to `POST /api/v1/auth/pair`. The request cannot choose an owner role; the browser cookie alone cannot mint a pairing code. Code consumption and owner-session creation are atomic. Pairing codes are single-use.
3. On success, issue the `onepath_session` owner cookie with `HttpOnly`, `SameSite=Strict`, and `Path=/api/v1`; enforce an **8-hour absolute TTL**, and set `Secure` when HTTPS is used. The loopback HTTP development path cannot provide the HTTPS `Secure` property; document that limitation and do not present it as remote-ready.
4. `GET /api/v1/auth/status` returns exactly `{ "contractVersion": 1, "authenticationRequired": true, "authenticated": <boolean>, "session": null | { "principalId": "<string>", "clientId": "onepath-browser", "expiresAt": "<ISO-UTC>" } }`. The unauthenticated form uses `authenticated: false` and `session: null`; it exposes no other owner data. `POST /api/v1/auth/logout` requires authentication and same-origin validation, then revokes the session.
5. Browser requests, including downloads and SSE, use the cookie normally. Never put credentials in URLs, browser local storage, provider API-key UI, or agent manifests. A future Tauri integration must use a secure native bridge; it is not part of this interface activation.

The server creates a stable owner ID. Persist only hashed bootstrap epochs, pairing codes, and session records in the existing SQLite database. Pair-code consumption plus session creation is one transaction. A server epoch change on restart invalidates older codes and sessions. Do not expose raw credentials or session tokens in API JSON, URLs, browser local storage, or logs; the one-use pairing code is returned only by the trusted-CLI bootstrap response described above.

Authentication middleware covers private API requests in source. The SSE handler also checks session validity before replay, each event-batch read, and each event yield; revocation ends only the response stream and does not cancel the run. This is source-reviewed, not a live production SSE test. The fake-client test for expiry/resume does not prove server-side stream enforcement dynamically.

### Internal caller-owned source-session proof

`LocalAuthStore.get_session_metadata(token, connection=None)` supports an optional caller-supplied SQLite connection. When supplied, it must be an active transaction on the store's configured database; the store leaves transaction completion and connection lifetime to the caller. The returned session fingerprint is derived from the stored token hash, not copied from caller metadata.

`LocalAuthService.authenticate_session(token, connection=...)` is an internal proof seam for a future source operation. It revalidates the live cookie session and returns the authenticated owner, stored client identity, and non-secret row-derived session fingerprint. The stored client remains `owner-browser`; the public `AuthenticatedOwner` continues to expose `onepath-browser`. This preserves existing public authentication behavior.

The source-operation caller must open the configured database, begin `BEGIN IMMEDIATE` before session proof, and keep that unit of work through approval consumption, source writes, and transaction completion. This ordering serializes the operation against logout and bootstrap-epoch rotation. The proof/store do not commit, roll back, or close the caller's connection; after a SQLite lock-upgrade failure, the caller must roll back and start a new unit of work. The method is internal only: no external source route or source capture is wired to it. It does not attest that caller-provided source metadata, Git state, or exclusions are correct and does not establish sandboxing or R1 completion.

Recorded bounded evidence: all 7 source-session transaction tests and 6 local-auth regression tests passed, with compile checks. The independent source-session review also passed its 13 focused tests and additional tampered-principal, redacted-cookie, and caller-transaction probes. These checks do not establish external route/capture integration or live-server behavior.

## Credential and worker boundaries

Protect the bootstrap file with a Windows DACL granting only the owner and SYSTEM, and verify file identity/handles and reject reparse-point substitution. On POSIX, require owner-only `0700` parent-directory and `0600` file permissions, the expected UID, and no-follow opening. Windows `chmod` alone is not sufficient. Exact file location and owner command remain implementation decisions; the documentation does not invent them.

The owner OS account and an administrator-controlled host are trusted. A private file does not defend against compromised code running as the same user. Untrusted workers therefore require OS-enforced AppContainer or namespace isolation and must not inherit access to the bootstrap file, its environment variables, or handles. A future generic application grant and scoped DevHub client authorization are separate work; owner pairing is not cross-app authorization.

## Owner operator guide — development checkout only

The CLI module is `app.owner_pairing`; it accepts an optional `--state-dir` and has no subcommands. From the repository's `backend` directory, the Windows development checkout can inspect its real help without contacting the server:

```powershell
Set-Location -LiteralPath '<OnePath-root>\backend'
& '..\.local\runtimes\application\venv\Scripts\python.exe' -m app.owner_pairing --help
```

To request a pairing code after the owner credential and server origin have been configured by OnePath, use the same module and optionally point `--state-dir` at the existing trusted application state directory:

```powershell
& '..\.local\runtimes\application\venv\Scripts\python.exe' -m app.owner_pairing --state-dir '<existing trusted state directory>'
```

The CLI reads the server origin and bootstrap secret from the verified credential record; it has no `--origin` option. It disables proxy use and redirects, uses a five-second timeout, and prints only the one-use code and its expiry. Never paste the master bootstrap secret into the browser. When the pairing UI is mounted and accepted, enter only the one-use code in the pairing dialog. These commands are source-derived guidance, not a claim that the checkout's CLI or production UI has been run. Linux invocation and installation paths remain unverified.

**Expiry-format discrepancy:** the interface accepts UTC timestamps ending in `Z` or `+00:00` and rejects non-zero offsets. The current CLI source requires an `expiresAt` string to end in `Z`, so it rejects the other valid UTC form. Backend must align the CLI parser with the frozen interface before claiming format compatibility.

## Evidence and activation gates

| Evidence | Result | Limit |
| --- | --- | --- |
| CAE R0 independent report | 13 tests and independent bounded PASS; [report](../.local/verification/cae-v23-r0-final.md) | Contract/documentation only; no HTTP authentication, runtime, sandbox, or product acceptance. |
| In-process authorization fence | 8 checks and independent bounded PASS, as reported in the Architecture work order | Does not establish HTTP authentication or OS isolation. Its receipt path was not supplied with this interface assignment. |
| Restart-quarantine review | 11 tests and independent source review PASS; [report](../.local/verification/cae-v23-r1-restart-risk.md) | Preserves unknown-work quarantine; positive native quiescence proof and a recovery resolver remain unimplemented. |
| Credential/session risk retest | 13 passed; 1 POSIX-only check skipped on Windows. Independent retest fixed the reproduced Windows-junction and strict-UTC defects; [report](../.local/verification/cae-v23-r1-auth-bootstrap-risk2.md) | Covered only the earlier A/C findings. It did not re-review B/D/E, prove POSIX behavior, or establish OS sandboxing. |
| API integration review, attempt 1 | Independent source review plus 9 fake-ASGI/temporary-SQLite API tests passed; [report](../.local/verification/cae-v23-r1-auth-integration-risk.md) | No live listener/provider/owner DB; synthetic event route did not exercise production SSE. |
| Backend integration review, attempts 1 and 2 | Scoped independent PASS: 9 fake-ASGI/temporary-SQLite API tests; 25 temporary-state startup/CLI/quarantine tests; production event-handler harness passed unauthenticated denial, cursor resume, and temporary-session revocation/expiry checks; [report](../.local/verification/cae-v23-r1-auth-integration-risk.md) | No live server, network SSE, provider, owner DB, or live run. These results do not establish OS isolation or release acceptance. |
| Frontend source checks | The [browser-checks log](../.local/verification/cae-v23-r1-auth-browser-checks2.log) records 15 syntax checks and 10 test modules/49 passing assertions; the [hash manifest](../.local/verification/cae-v23-r1-auth-browser-checks2.sha256.txt) binds those inputs. The later Escape-guard correction passed targeted fake checks in [correction log](../.local/verification/cae-v23-r1-auth-browser-correction3.log) with [source manifest](../.local/verification/cae-v23-r1-auth-browser-correction3.sha256.txt). | Fake DOM/client checks and source-reviewed view hook; not actual dialog focus or browser acceptance. |
| Actual browser review | **FAIL** on the reviewed revision: Escape dismissed the locked pairing dialog. The later internal-browser attempt was **BLOCKED** because `iab` was unavailable; [review record](../.local/verification/cae-v23-r1-auth-browser-review.md) | The correction's fake checks do not retest the actual browser defect. No Edge fallback or current browser acceptance. Other pairing, logout, expiry, and responsive criteria remain unverified. |
| Backend correction and integrated source | Backend reports correction 4 with 65 affected tests passing; report 1's source hashes match the correction manifest | Test receipt for correction 4 was not included in this documentation assignment. Hash/source agreement does not prove live operation. |

Source wiring and the scoped backend integration review are recorded above. Remaining gates include aligning the CLI UTC parser with the contract, real-browser verification of the Escape-guard correction and remaining pairing/UI criteria, live server acceptance, native POSIX credential behavior, OS sandbox attestation, and positive recovery resolution. Preserve restart-quarantine precedence. Separate source-wired behavior, fake/local tests, independent review, live browser/native behavior, packaged behavior, and owner acceptance; none implies another.
