# Mobile Banking Validation Service

## Responsibility
Validates that the applicant's mobile number (MSISDN) and CNIC are **eligible for a wallet** according to the bank's own mobile-banking platform: the number is not already bound to an active wallet, the CNIC does not exceed the permitted number of accounts, the customer is not blacklisted/closed-for-cause, and the MSISDN format/prefix belongs to a supported operator. It wraps the legacy Mobile Banking System (an internal core system) behind a stable, versioned API so the orchestrator never talks to the legacy interface directly.

## APIs Exposed
Sync (REST/gRPC via mesh; caller = Onboarding Orchestrator only):

| Method | Path | Request | Response |
|---|---|---|---|
| `POST` | `/v1/eligibility/validate` | `{requestRef, caseId, msisdn, cnic, channel}` | `{result: ELIGIBLE \| INELIGIBLE \| DEGRADED, reasonCodes[], existingWalletRef?, checkedAt, source: LIVE \| CACHE}` |
| `GET` | `/v1/eligibility/{requestRef}` | — | Replay of a previous result (idempotency) |
| `GET` | `/health/ready`, `/health/live` | — | Readiness includes Mobile Banking System reachability (non-blocking) |

Async:
- Consumes `provisioning.events.AccountProvisioned` to update its local `msisdn_registry` (marks MSISDN as bound) so future duplicate checks work even if the legacy system lags.
- Publishes nothing customer-facing (see Notifications).

## Dependencies
- **Upstream**: Onboarding Orchestrator.
- **Downstream**: Mobile Banking System (internal, SOAP/REST adapter, on-prem) — the authoritative wallet registry.
- **Datastore**: Eligibility DB (PostgreSQL, own schema) + a local read-through cache of recent lookups.
- **Event bus**: Kafka consumer group `mb-validation` on `provisioning.events`.
- **Platform**: Istio mTLS, K8s DNS, OpenTelemetry, Prometheus, EFK.

## Data Owned
- `eligibility_check` (requestRef, caseId, msisdnHash, cnicHash, result, reasonCodes, source, checkedAt).
- `msisdn_registry` (projection: MSISDN → wallet status/lastSeen, fed by legacy sync + events). This is a **read model / cache**, the Mobile Banking System remains the system of record for wallets.
- Operator prefix table (supported MNO ranges).

MSISDN and CNIC are stored hashed (HMAC) for lookups; clear values only in memory during the call.

## Resilience & Failure Handling
- **Circuit Breaker** on the Mobile Banking System adapter (Resilience4j; window 20 calls, 50 % failure or 60 % slow (> 2 s) opens it for 30 s).
- **Retry with exponential backoff**: 2 retries, base 200 ms, jitter; legacy call is a read, so safe to retry.
- **Timeout**: 2.5 s per legacy call (orchestrator's budget for this step is 3 s).
- **Bulkhead**: fixed thread pool (size = legacy system's contractual concurrency) isolating legacy calls from the HTTP server threads; a semaphore rejects excess calls immediately rather than queueing.
- **Graceful degradation / fallback**: when the breaker is open or the call times out, answer from the local `msisdn_registry` projection and return `result = DEGRADED` (eligible-as-far-as-we-know). The orchestrator treats `DEGRADED` as pass-for-now but schedules a mandatory live re-check before the Account Provisioning step; if the re-check is still impossible the case goes to `IN_REVIEW`. This is a **non-critical** validation, so it never blocks the customer outright.
- **Idempotency**: results are keyed by `requestRef`; repeated calls return the stored result.
- **DLQ**: `provisioning.events` consumer parks unprocessable messages in `provisioning.events.mb-validation.DLQ`.

## Notifications Triggered
None directly — results are returned synchronously to the orchestrator, which publishes `OnboardingStepCompleted{MOBILE_BANKING_VALIDATION}` or `OnboardingStepFailed{MOBILE_BANKING_VALIDATION, reasonCode}` (e.g. `MSISDN_ALREADY_REGISTERED`, `CNIC_ACCOUNT_LIMIT_REACHED`).
