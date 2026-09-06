# NADRA / KYC Integration Service

## Responsibility
Performs the **regulatory identity verification (KYC)** of the applicant against Pakistan's **NADRA** (National Database and Registration Authority) and, where required, a third-party KYC vendor:

- **NADRA VeriSys** — verifies CNIC number + name/father's name/DoB and returns the citizen's registered data and photograph.
- **NADRA BioVeriSys** — fingerprint biometric match (mandatory for full wallet tiers under SBP branchless-banking regulations).
- **Third-party KYC vendor** (e.g. Jumio / Onfido / a local provider) — CNIC card OCR, liveness detection and face-match of the selfie against the CNIC photo, for tiers where fingerprint capture is not available on the device.

The service normalises the different provider contracts into a single `KycVerified / KycRejected / KycInconclusive` outcome, stores the verification evidence securely (encrypted) for the regulatory retention period, and shields the rest of the platform from provider-specific formats, credentials and quirks. All outbound calls go through the **External Integration Gateway**.

## APIs Exposed
Sync (REST/gRPC via mesh; caller = Onboarding Orchestrator):

| Method | Path | Request | Response |
|---|---|---|---|
| `POST` | `/v1/kyc/verifications` | `{requestRef, caseId, cnic, fullName, fatherName, dob, tier, documents: {cnicFrontRef, cnicBackRef, selfieRef}, biometric?: {template, deviceId}}` | `{result: VERIFIED \| REJECTED \| INCONCLUSIVE, checks: {cnicValid, nameMatch, dobMatch, biometricMatch?, faceMatch?, liveness?}, verificationId, providerRefs (masked), expiresAt}` |
| `GET` | `/v1/kyc/verifications/{verificationId}` | — | Replay / evidence summary (masked) |
| `GET` | `/v1/kyc/verifications/by-cnic/{cnicHash}/latest` | — | Reuse a still-valid verification (re-onboarding) |

Documents are referenced by object-storage keys (uploaded via the gateway to a KMS-encrypted bucket); binary data never flows through Kafka.

Async — **published** (topic `kyc.events`, via outbox): `KycVerificationCompleted{caseId, verificationId, result}` — used by the audit trail and by the Digital Identity Service to bind the verified identity.

## Dependencies
- **Upstream**: Onboarding Orchestrator.
- **Downstream (external, via External Integration Gateway)**: NADRA VeriSys, NADRA BioVeriSys, third-party KYC vendor API. Authentication: mTLS client certificates + API keys/franchise IDs issued by NADRA, stored in Vault.
- **Object storage** (S3-compatible, SSE-KMS) for document images; **KMS** for envelope encryption of stored responses.
- **Datastore**: KYC DB (PostgreSQL, own schema, column-level encryption for PII).
- **Event bus**: Kafka producer `kyc.events`.

## Data Owned
Source of truth for the *verification outcome and evidence*:
- `kyc_verification` (verificationId, caseId, requestRef, cnicHash, result, checks JSON, tier, provider, providerTransactionIds, verifiedAt, expiresAt).
- `kyc_evidence` (encrypted NADRA response payload, photo hash, biometric-match score — **never the raw fingerprint template**, which is discarded after the call).
- `document_hash` (SHA-256 of submitted images to detect reuse of the same document across applications).
- Provider configuration (endpoints, quotas, certificate references).

Retention: 10 years after relationship end (SBP AML/CFT regulations); PII encrypted at rest with per-record data keys.

## Resilience & Failure Handling
- **Circuit Breaker** per provider (NADRA VeriSys, BioVeriSys, KYC vendor each have their own Resilience4j breaker; the External Integration Gateway adds Envoy outlier detection). Opening one breaker does not affect the others.
- **Retry with exponential backoff**: 2 retries, base 500 ms, jitter, only on timeouts / 5xx / connection resets. NADRA transactions are charged per call, so retries reuse the same `providerTransactionId` where the API supports it and are capped strictly.
- **Timeout**: 8 s per provider call (NADRA latency is typically 2–5 s); orchestrator step budget 10 s.
- **Bulkhead**: separate connection pools and concurrency limits per provider matched to the contractual TPS quota; excess requests are rejected fast (`429`) rather than queued, so the orchestrator can retry later.
- **Graceful degradation**: KYC is a **critical, regulatory step — it fails closed**. There is no synthetic "pass". Degradation options are limited to:
  - Reuse an existing, non-expired verification for the same CNIC (re-onboarding / tier upgrade).
  - If BioVeriSys is down but VeriSys is up, the case can proceed in a **lower wallet tier** (Level-0/1 limits) with `INCONCLUSIVE{BIOMETRIC_PENDING}`; the orchestrator marks the account for mandatory biometric completion within the regulatory window.
  - Otherwise the orchestrator keeps the step pending with retries until the saga deadline, then fails the case with `KYC_UNAVAILABLE` and the customer is asked to retry later.
- **Idempotency**: `requestRef` unique; duplicate calls return the stored result and do not re-hit NADRA (cost + quota protection).
- **Rate limiting** toward NADRA aligned with the licence quota; token-bucket in the gateway.
- **Security**: PII masked in logs/traces (`cnic: 42101-*******-1`), request/response audit stored encrypted, credentials rotated via Vault.
- **DLQ**: outbox publication failures surface in `kyc.events.DLQ`.

## Notifications Triggered
None directly. The orchestrator maps the outcome to `OnboardingStepCompleted{KYC}`, `OnboardingInReview{KYC_INCONCLUSIVE}` (e.g. face-match below threshold → manual document review) or `OnboardingStepFailed{KYC, reasonCode}` (`CNIC_INVALID`, `CNIC_EXPIRED`, `BIOMETRIC_MISMATCH`, `DOCUMENT_REUSED`), which drive the customer notifications.
