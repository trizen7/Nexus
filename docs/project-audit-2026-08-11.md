# Nexus project audit - 2026-08-11

This file is the recovery record for the whole-project audit requested on
2026-08-11. Keep it updated after every review, fix, and verification round so
the work can resume safely after an interruption.

## Non-negotiable boundaries

- Hermes remains an original, read-only external dependency.
- Nexus integrates with Hermes only through its public HTTP API.
- Tests use mocks and contract fixtures; they do not manage a real Hermes
  installation, process, configuration, storage, or logs.
- All generated or modified files remain inside Nexus-owned directories.
- Secrets, tokens, keys, certificates, and user logs must not enter reports or
  build artifacts.
- The Android Debug APK is overwritten at `成品/test/app-debug.apk`.
- Android native packaging is limited to `arm64-v8a`.

## Recovery status

- Goal: whole-project audit, confirmed fixes, multiple independent checks, and
  final artifact verification.
- Current phase: complete. All repair rounds, independent reviews, final gates,
  and Debug APK delivery/inspection have finished.
- Current worktree is intentionally dirty and is the audit baseline. Do not
  revert pre-existing changes.
- The initial repository inventory and dirty-worktree snapshot were captured
  before repairs, excluding Gradle caches, build outputs, local test state, and
  finished goods. The worktree remains intentionally dirty with the reviewed
  audit changes and pre-existing user changes; current `git status` is the
  authoritative file list.
- `git diff --check`: passed; only line-ending conversion warnings were emitted.

## Last known verification baseline

These results predate the full-project audit and must be repeated after fixes:

- Android unit tests: 197 passed.
- Android Lint: passed.
- Android Debug build: passed.
- Gateway tests: 203 passed, 11 skipped.
- APK v2 signature: valid.
- APK ABI: `arm64-v8a` only.
- APK size: 50,658,084 bytes (48.31 MiB).
- APK SHA-256: `ED5BDB2D0EB7D031244B745F00B0D34DC769BC9D0AF9003C0BAC7576C70C2AF9`.

## Audit plan

- [x] Establish repository baseline, dependency inventory, test matrix, and
  reproducible commands.
- [x] Complete independent Android audit.
- [x] Complete independent Gateway, deployment, reset, and upgrade audit.
- [x] Complete independent tests, dependencies, licensing, CI, release, docs,
  and artifact audit.
- [x] Consolidate findings by severity and reproduce confirmed defects.
- [x] Apply first-round fixes in isolated, reviewable groups with focused tests.
- [x] Run first full regression.
- [x] Perform a second independent review and fault-injection round, including
  voice capture, interruption, echo, streaming, TTS failure, cancellation, and
  lifecycle behavior.
- [x] Run final full regression and security checks.
- [x] Rebuild `成品/test/app-debug.apk` and verify signature, ABI, size, and hash.
- [x] Record residual risks and final conclusions.

## Test matrix

| Area | Command or method | Baseline | Final |
| --- | --- | --- | --- |
| Gateway and contracts | `.local-test/venv/Scripts/python.exe -m pytest tests -q` from `gateway` | 203 passed, 11 skipped, 5 warnings | 263 passed, 12 skipped |
| Web contract | `node gateway/tests/web_contract_test.js` | passed | passed |
| Android unit tests | `gradlew.bat testDebugUnitTest --no-daemon` from `android` | 197 passed | 241 passed after offline-ASR accuracy follow-up |
| Android Lint | `gradlew.bat lintDebug --no-daemon` from `android` | passed | passed; 0 errors and the expected arm64-only ChromeOS ABI warning |
| Android Debug APK | `gradlew.bat assembleDebug --no-daemon` from `android` | passed | passed; forced rebuild and delivered |
| Release validation | `.local-test/venv/Scripts/python.exe scripts/build_release.py --validate-only` | failed: Android 0.1.11, Gateway 0.1.10 | passed: all 0.1.11; Android code 27 |
| Secret scan | `.local-test/venv/Scripts/python.exe scripts/scan_repository_secrets.py` | failed: 51 large-file findings, no credential-pattern finding | 971 blobs, 0 findings |
| APK inspection | signature, ZIP ABI inventory, size, SHA-256 | valid/arm64 only | v2 valid; arm64 only; 50,654,899 bytes; `98E48F41D599391931F31F3A80F04BDB1BEE60F956DA4F7192E75E204A97EA6A` |

## Findings and decisions

The three independent read-only audits completed without a P0 finding. The
confirmed P1/high groups were fixed in non-overlapping batches:

- Android stale stream events could mutate a newer run after interruption;
  streaming HTTP cancellation did not cancel the underlying call; runtime model
  verification accepted missing provider/model fields.
- Voice messages captured while a run was stopping could be overwritten through
  shared input state; local ASR capture and ONNX decode shared one thread; AEC
  failure was silent; uploads were not fully cancellable; unknown provider sizes
  could bypass the 50 MiB limit.
- Gateway session-run admission was non-atomic; ephemeral recordings could remain
  after exception/cancellation; generic SSE errors could be overwritten as
  completed; multiple queues/buffers and attachment expansion were unbounded.
- Generic Gateway startup accepted write paths outside a single Nexus data root
  and could chmod hard-linked files; the local test credentials command printed
  a plaintext password.
- Android/Gateway/release versions differed; the sherpa AAR was untracked with no
  clean-checkout acquisition path; online Edge TTS was missing privacy disclosure
  and third-party distribution notices; the secret scan was blocked by unignored
  Gradle caches; Debug APK delivery was not automated.

Confirmed P1 items that cannot be represented as complete without external
capability or policy work remain explicit release risks: true remote Hermes run
cancellation must use a documented public API rather than treating an HTTP
disconnect as cancellation, and LGPL obligations for bundled server artifacts
must be satisfied before a formal public release.

### Baseline findings resolved during the audit

- **Release blocker:** Android `versionName` is `0.1.11`, while Gateway,
  Compose, README, fnOS manifest, and release-contract fixtures remain at
  `0.1.10`. `scripts/build_release.py --validate-only` exits with status 1.
- **Security-gate noise:** the secret scanner reports 51 large worktree blobs.
  Fifty are inside unignored `android/.gradle-user-home/` caches; the remaining
  file is the intentionally bundled `android/app/libs/sherpa-onnx-1.13.5.aar`.
  No secret content rule or suspicious credential filename was reported. The
  scanner still exits with status 1, so its release gate cannot currently pass.
- **Test maintenance:** Gateway tests pass with five aiohttp deprecation
  warnings caused by changing application state after startup. These are not
  runtime failures but should be removed before the API behavior becomes an
  error in a later aiohttp release.

### First repair round

- Gateway added atomic per-session run reservations, 90-second transcription
  timeout, unconditional ephemeral media cleanup, and terminal SSE outcome
  tracking. Focused tests passed; the first post-fix Gateway regression passed
  with 215 tests and 15 skips.
- Gateway startup now requires all writable paths under a resolved Nexus data
  directory and rejects hard-linked secure files before chmod/read. The local
  credentials command no longer prints passwords. The focused boundary and
  deployment suite passed with 33 tests and 6 platform skips.
- Android streaming requests now cancel their OkHttp calls, stale run generations
  are ignored on the Main dispatcher, and selected-model verification rejects
  missing runtime provider/model fields. Focused 65-test and full unit-test runs
  passed in the owning repair batch.
- Local ASR capture now runs on a dedicated urgent-audio thread and feeds ONNX
  decoding through a bounded 500 ms PCM frame buffer. Overflow aborts and
  retries instead of submitting incomplete text. Buffer tests passed. Local AEC capability is
  surfaced so unsupported devices can reject speakerphone instead of silently
  running degraded full duplex.
- TTS temporary-file I/O moved off the Main dispatcher, MediaPlayer preparation
  became asynchronous, and preparation/playback watchdogs plus bounded queues
  prevent indefinite stalls. The final Android regression passed.
- Product-test Gateway upgrades now require one exact valid `SHA256SUMS.txt`
  entry, copy the selected ZIP into a managed temporary directory, verify and
  extract only that copy, and remove it in `finally`. Upgrades retain the old
  app, venv, dependency state/lock, and deployment state until the new Gateway
  passes readiness checks. Failures stop the partial new service, copy the full
  backup set back so restoration remains retryable, and attempt to restart the
  old Gateway. Focused contract and fault-injection tests passed with 26 tests;
  the final Gateway regression passed with 240 tests and 12 skips.
- Android voice sends now carry immutable text/profile/session payloads instead
  of mutating the shared composer. A distinct STOPPING state serializes
  barge-in, merges utterances in arrival order, and restores timed-out text by
  appending it to the existing draft. A later independent review found that the
  stop HTTP request could arrive after a new run and that lifecycle cleanup
  could discard a pending payload. The stop call is now cancellable and strictly
  completes before inactive polling; undispatched payloads are restored to their
  scoped drafts without duplicating an owned send.

### Second review and repair round

Independent Windows fault injection found two remaining Nexus write-boundary
violations. A junction beneath the product-test managed root could redirect a
managed delete outside that root, and a hard-linked `session_media.json` could
redirect a Gateway metadata write outside `NEXUS_DATA_DIR`. The same review
found inconsistent model/reasoning locking between streaming and non-streaming
chat, ambiguous local-versus-remote stop semantics, slow/leakable SSE subscriber
cleanup, and missing TTS concurrency/output-byte limits. These findings are
confirmed and were closed by the second repair batch.

### Third write-boundary review

The third review expanded the write-boundary threat model from static path
validation to runtime directory replacement. The product-test PowerShell
controller now routes Nexus-owned directory creation, deletion, movement,
copying, JSON/text persistence, process records, and redirected process logs
through managed helpers. These helpers recheck every existing ancestor directly
before the filesystem operation, reject reparse points and existing hard-linked
file targets, and persist text through a same-directory, exclusive UUID
temporary file followed by `File.Replace` or `File.Move`.

The Gateway pins the device/file identity of `NEXUS_DATA_DIR` and the media
root, walks each target parent without resolving through symlinks or junctions,
and rejects identity changes before creating or replacing a file. On POSIX,
temporary-file creation, replacement, and guarded deletion use a verified
directory descriptor with `dir_fd` and `O_NOFOLLOW` where available. On Windows,
Python and Windows PowerShell standard APIs do not expose a complete `openat`
equivalent for these operations. Nexus therefore uses exclusive unpredictable
temporary names, fixed directory identities, immediate pre-operation rechecks,
single-link target enforcement, and user-owned runtime directories. A malicious
process already running as the same OS account could still race the final
Windows check and system call; defending against that stronger threat requires
native handle-relative Windows filesystem code or running Nexus under a
separate restricted account. Static junction/hardlink/root-replacement escapes
covered by the fault-injection suite are rejected without touching external
files. The final Gateway regression, including these contracts, passed with
251 tests and 12 platform/capability skips.

### Final Android voice review

Independent event-sequence review reproduced a delayed stop request that could
stop a newer run, lifecycle paths that discarded undispatched recognized text,
local ASR gaps caused by recreating `AudioRecord` after each endpoint, and a
barge-in path that waited for ASR partial text. The final implementation keeps
the local recorder and decoder session alive across endpoints, uses a bounded
500 ms PCM queue/pre-roll, and resets only the online stream. Playback pause is
gated by calibrated acoustic activity rather than any partial result. The local
gate samples every calibration frame, uses the low percentile as its route noise
floor, and permits a provisional trigger only after observing a lower baseline.
Steady loud startup noise therefore becomes the floor instead of triggering a
pause, while a later sustained rise still triggers after three frames. The
system-recognizer fallback learns ambient RMS while playback is idle and freezes
that baseline during playback, so speech beginning with the first playback RMS
callback is not absorbed into the threshold. Short playback echo without
acoustic double-talk evidence is suppressed.

Fatal local-ASR failure releases its microphone before system-ASR fallback.
AEC `enabled` is exposed only as a capability signal, not a quality guarantee;
speakerphone is disabled when the required local AEC path is unavailable. A
one-shot logout recovery slot is scoped by normalized server, username,
profile, and session, and restores only to the same account/profile after draft
loading. The final forced Android regression passed all 239 tests, Lint, and
Debug assembly; focused detector reviews also passed after both noise-gate
repairs.

### Final model-routing and cleanup review

The last independent Gateway review found that selected-model catalog and lock
requests could wait indefinitely before the first SSE event, legacy `/chat`
did not share stream admission, and the selected-model adapter validated the
requested model without strictly validating the actual runtime. Model catalog
and lock operations now have independent bounded timeouts. Legacy and streaming
chat share atomic per-session admission, observable run state, and local stop
semantics while legacy responses retain their original HTTP status, body, and
headers. Selected-model SSE and JSON responses require actual provider/model,
route source, lock status, and requested model to agree; unverified output is
rejected without forwarding upstream details.

The same review reproduced a Windows cleanup race for injected thread-based
transcribers. Timeout and cancellation now leave the worker task shielded,
wait for it to release the recording, and then retry ephemeral deletion in a
tracked background cleanup task. Cleanup errors cannot replace the original
504 or cancellation result. Gateway cleanup also cancels and awaits detached
run tasks. The final Gateway regression passed 263 tests with 12 capability or
platform skips.

## Completed fixes before this audit

- TTS retries recoverable upstream and I/O failures.
- In-flight TTS jobs and HTTP calls are cancellable on interruption.
- Prepared TTS audio is bounded to two segments.
- Media decode failures are surfaced instead of treated as successful playback.
- TTS failures are nonfatal and do not stop microphone capture.
- Closed speech queue sends roll back pending counters.
- Voice interruption waits for the prior run to become inactive before sending
  the interrupting utterance.
- Gateway run cancellation waits for cleanup before returning.
- The obsolete punctuation-model feature and stale downloaded data were removed.

## Residual risks and follow-up

- JVM tests cannot measure OEM AEC echo-return loss or real double-talk recall.
  Run a device matrix at three playback/microphone levels across earpiece,
  speakerphone, wired headset, and Bluetooth before treating thresholds as
  production-calibrated.
- A purely amplitude-based interruption gate cannot distinguish speech that is
  already present in the first uncalibrated sample from steady noise at exactly
  the same level. Both ASR paths continue consuming that audio, but high-noise
  interruption sensitivity and the current 9 dB local rise threshold still need
  device-corpus tuning.
- Recognition quality and first-token latency still need a recorded Chinese
  corpus A/B test. X-ASR's current streaming Chinese/punctuation model is a
  candidate, but this audit intentionally did not replace the accepted model.
- Edge TTS remains an online best-effort dependency. Retries, timeouts,
  cancellation, queue bounds, and nonfatal recovery are implemented, but a
  sustained outage can still skip a spoken segment. Android system
  `TextToSpeech` fallback is the next availability improvement.
- True low-latency speech output still requires Gateway audio streaming and
  progressive Android playback; current text SSE is streaming, but each TTS
  chunk is generated before playback.
- Hermes remote inference cancellation cannot be asserted without a documented
  original Hermes public stop API. Nexus now reports local forwarding canceled
  and remote status unknown instead of claiming remote completion.
- A product-test upgrade interrupted by machine/process loss can leave
  `upgrade.previous` without a supported manifest-driven recover command. The
  verified in-process transactional rollback remains intact.
- Real Linux PyInstaller/fnOS packaging, container LGPL contents, and full FPK
  verification require CI/Linux because Docker is unavailable on this host.
- On Windows, a malicious process already running as the same OS account retains
  a very small final check/system-call filesystem race. Native handle-relative
  APIs or a separate restricted service account are required for that stronger
  threat model.

## Final artifact

- Path: `成品/test/app-debug.apk`
- Version: `0.1.11` (`versionCode 27`)
- Size: 51,228,950 bytes
- SHA-256: `0DB431E29299269577028ED84B5D66375311058B48A34B79790734A4A9B554E6`
- Signature: APK Signature Scheme v2 valid, one signer
- Native ABI: `arm64-v8a` only, five native libraries
- Legal assets: `APACHE-2.0-LICENSE.txt` and `THIRD_PARTY_NOTICES.md`

## Audit log

- 2026-08-11: Created persistent goal and five-phase plan.
- 2026-08-11: Started three parallel, read-only audits: Android;
  Gateway/deployment; and quality/release/documentation.
- 2026-08-11: Captured repository inventory, dirty-worktree diff summary,
  dependencies, build entry points, test entry points, and current APK metadata.
- 2026-08-11: Repeated the first baseline test round. Gateway: 203 passed and 11
  skipped; Android: 197 passed, Lint passed, Debug build passed; Web contract:
  passed. Release validation and secret scan failed for the reasons recorded
  above.
- 2026-08-12: Completed all three first-pass audits, consolidated findings, and
  started isolated repair batches. Recorded the first Gateway and Android repair
  results and added focused data-boundary and PCM-buffer tests.
- 2026-08-12: Completed deployment repair batch C. PowerShell parser validation,
  strict-checksum execution cases, managed-copy TOCTOU simulation, retryable
  rollback fault injection, scoped diff checks, and the 240-test Gateway
  regression all passed. Independent review confirmed the two follow-up risks
  were closed. Hermes was not modified or managed.
- 2026-08-12: Completed the first Android voice-state repair and its 214-test,
  Lint, and Debug build gate. Independent review then reproduced two additional
  stop/lifecycle races, so that build was not the final artifact and a second
  Android repair was started and later completed as recorded below.
- 2026-08-12: A second independent Gateway/deployment review reproduced a
  junction-based managed-path escape and a hard-link-based media-index write
  escape, plus model-locking and resource/state hardening findings. Started a
  second Gateway repair batch. No real Hermes installation or process was
  accessed.
- 2026-08-12: The third write-boundary review added runtime data/media identity
  pinning, managed PowerShell write primitives, Windows junction/hardlink fault
  injection, and explicit documentation of the remaining same-account Windows
  check/use race. No Hermes file, configuration, or process was accessed.
- 2026-08-12: Reworked local voice capture to remain active across decoder
  endpoints, added calibrated acoustic barge-in with pre-roll, closed delayed
  stop and draft-loss races, added local-to-system ASR failure fallback, and
  independently reviewed the final state. The final forced Android gate passed
  233 tests, Lint, and Debug assembly.
- 2026-08-12: A fourth independent review found startup-high-noise false
  interruption in both local and system-recognizer gates, unbounded selected
  model preflight, incomplete legacy-chat admission/runtime verification, and a
  Windows thread-transcriber cleanup race. These were repaired with focused
  fault-injection tests before the final gate.
- 2026-08-12: Final project gates passed: Android 239 tests, Gateway 263
  passed/12 skipped, Web
  contract passed, release versions validated at 0.1.11, and the secret scan
  reported 971 blobs with zero findings. Atomically delivered and inspected the
  Debug APK recorded above. The artifact uses v2 signing, contains only arm64
  native libraries, and carries both required legal assets. The audit is
  complete; remaining external/device/platform risks are listed explicitly.
- 2026-08-12: A follow-up replaced the chat composer speech Activity with
  inline press-and-hold recognition, disabled call-specific injected audio for
  ordinary dictation, and added the verified X-ASR 480 ms Chinese/English
  punctuation model as an optional download. Android again passed 239 tests,
  Lint, and Debug assembly; the updated artifact metadata is recorded above.
- 2026-08-12: Accuracy follow-up added independently selected SenseVoice 2025
  and Paraformer 2025 offline utterance models for press-and-hold dictation.
  Streaming Transducers used by calls now use modified beam search and longer
  endpoint tolerance. Models remain verified on-demand downloads and are not
  bundled into the APK.
- 2026-08-16: Repository takeover and cleanup. Full regression gates passed
  (Gateway 282 passed/12 skipped; Android 253 unit tests, Lint and Debug APK;
  web contract; release validation at 0.1.17/versionCode 33; secret scan 986
  blobs with zero findings). The 0.1.17 feature worktree (ASR/TTS/voice calls,
  Gateway hardening, scripts, docs) was split into four semantic commits and
  pushed to `origin/main` (`23538f9..f84efa6`). Disposable intermediates
  (`.research/`, `.tools/`, `.nexus-test/`, `android/app/build`,
  `android/.gradle/`) were removed; `.local-test/` and `成品/` acceptance
  environments were kept. Line endings were normalized (`* text=auto eol=lf`
  default in `.gitattributes`, repo-level `core.autocrlf=false`) and the
  change pushed as `a99af61`.
- 2026-08-16: Version history gap resolved by record. Versions 0.1.11 through
  0.1.16 exist only as local build products under `成品/v0.1.12` to
  `成品/v0.1.16` (each with its own `SHA256SUMS.txt`). They were built from an
  uncommitted worktree during the audit iteration, so no git commit or tag can
  be attached to them; they were never formal public releases. The first
  fully tracked release is 0.1.17: annotated tag `v0.1.17` was created on the
  verified release HEAD (`a99af61`) and pushed to `origin`. The release
  process in `docs/releasing.md` already requires tagging every version before
  publishing; the 0.1.11-0.1.16 gap is a one-time deviation, not a policy.
  Stale local branches `codex/pre-sync-0.0.2` and
  `codex/mobile-model-cron-management` were deleted.
