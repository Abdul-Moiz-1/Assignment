# Onboarding Orchestrator Service

## Responsibility
The Onboarding Orchestrator is the **Saga orchestrator** for the end-to-end customer onboarding workflow. It owns the onboarding state machine, decides which validations run (in parallel where possible), waits for manual-review outcomes, drives the sequential provisioning steps, and executes **compensating transactions** in reverse order when any step fails. It is the *only* component that publishes customer-visible onboarding status events, which keeps the status stream consistent.

State machine (persisted per case):

```
RECEIVED → VALIDATING → [IN_REVIEW] → PROVISIONING → COMPLETED
                │              │              │
                └──────────────┴──────────────┴─→ COMPENSATING → FAILED
```

Step plan:
1. Persist case, publish `STARTED`.
2. Scatter-gather (parallel): Mobile Banking Validation (4a), SafeWatch (4b), Fraud (4c), NADRA/KYC (5a), PMD (5b).
3. Evaluate results: any hard reject → `FAILED`; any `REVIEW` → `IN_REVIEW` and wait for `compliance.review.completed` / `fraud.review.completed`.
4. Sequential provisioning: Digital Identity (6a) → Account Provisioning (6b) → Card Management (6c).
5. Publish `COMPLETED`.

## APIs Exposed
Sync (REST/gRPC, called by the API Gateway only — enforced by Istio `AuthorizationPolicy`):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/onboarding` | Create case; returns `202 {caseId, status}`. Honors `Idempotency-Key`. |
| `GET` | `/v1/onboarding/{caseId}` | Case status, per-step outcome (masked), next action for the customer |
| `POST` | `/v1/onboarding/{caseId}/resume` | Customer supplies corrected data after `ACTION_REQUIRED` |
| `POST` | `/internal/onboarding/{caseId}/cancel` | Back-office cancel → triggers compensation (admin scope) |

Outbound sync calls (all via mesh, mTLS): `POST /validate` on each internal validation service; `POST /verify` on NADRA/KYC and PMD services; `POST /identities`, `POST /accounts`, `POST /cards` and their `DELETE`/`revoke` compensations on the provisioning services.

Async — events **published** (topic `onboarding.status`, key = `caseId`, Avro):
- `OnboardingStarted`, `OnboardingStepCompleted{step}`, `OnboardingInReview{reason}`, `OnboardingStepFailed{step, reasonCode}`, `OnboardingCompensated`, `OnboardingCompleted{accountRef, cardRef}`, `OnboardingFailed{reasonCode}`.

Async — events **consumed**:
- `compliance.review.completed` (from SafeWatch), `fraud.review.completed` (from Fraud) → resume waiting saga.
- `provisioning.events` (`DigitalIdIssued`, `AccountProvisioned`, `CardIssued`) → reconcile against the sync response (detects lost responses).

## Dependencies
- **Upstream**: API Gateway.
- **Downstream (sync)**: Mobile Banking Validation, SafeWatch Compliance, Fraud Management, NADRA/KYC Integration, PMD Validation, Digital Identity, Account Provisioning, Card Management services.
- **Datastores**: Saga State Store (PostgreSQL — own schema), Redis cluster (in-progress session/state, idempotency short-circuit).
- **Event bus**: Kafka (`onboarding.status` producer via transactional outbox + Debezium; consumer of `compliance.review`, `fraud.review`, `provisioning.events`).
- **Platform**: Kubernetes DNS / Istio for discovery and mTLS; OpenTelemetry, Prometheus, EFK.

## Data Owned
Source of truth for the *workflow*, not for customer master data:
- `onboarding_case` (caseId, customerRef, status, createdAt, deadlineAt, channel, correlationId).
- `saga_step` (caseId, step, attempt, status, requestRef, responseSummary-masked, compensated flag).
- `idempotency_key` (key, requestHash, caseId, responseSnapshot, expiresAt).
- `outbox` (event payloads awaiting CDC publication).
- Redis: `onboarding:session:{caseId}` (wizard progress, partially entered data, TTL 24 h) — ephemeral; the Postgres state store is authoritative.

The orchestrator stores **references** (accountRef, cardToken, digitalId) but never PANs, biometrics or full KYC documents.

## Resilience & Failure Handling
- **Circuit Breaker** per downstream service (Resilience4j, one instance each; failure-rate threshold 50 % over sliding window of 20 calls; open 30 s; half-open 5 probes). Mesh-level outlier detection provides a second layer.
- **Retry with exponential backoff** (base 300 ms, ×2, max 3 attempts, full jitter) only for timeouts / 5xx / connection errors; **never** for business rejections (`422`). Every downstream call carries a deterministic `requestRef = hash(caseId, step, attemptGroup)` so retries are idempotent on the callee side.
- **Timeouts**: per step (validations 3 s, NADRA 8 s, PMD 5 s, CBS 10 s, CMS 10 s) plus a saga-level deadline (default 15 min for the automated path; `IN_REVIEW` cases may wait up to 72 h and are then auto-expired with `OnboardingFailed{REVIEW_TIMEOUT}`).
- **Bulkhead**: dedicated thread pool / connection pool per downstream so a slow NADRA call cannot exhaust capacity for Fraud or CBS calls; separate worker pools for "new cases" vs "resume after review".
- **Graceful degradation** (policy encoded in the step plan):
  - SafeWatch or Fraud unavailable (breaker open) → case goes to `IN_REVIEW` with reason `DEPENDENCY_UNAVAILABLE`; a deferred re-check is scheduled; customer is told the application is under review instead of being rejected.
  - Mobile Banking Validation unavailable → falls back to the eligibility cache (see that service); result flagged `DEGRADED` and re-verified before provisioning.
  - NADRA/KYC, PMD, Core Banking, Card Management unavailable → **fail closed**; no fallback for regulatory/critical steps. Case remains `VALIDATING`/`PROVISIONING` with retries until the deadline, then compensation.
- **Compensating transactions** (reverse order, each idempotent and retried until success or parked): `revokeCard` → `closeAccount` (CBS) → `revokeDigitalIdentity`. Compensation failures are written to `saga_step` with `COMPENSATION_STUCK` and alerted for manual intervention.
- **Idempotency**: `Idempotency-Key` from the gateway is stored with a hash of the request; replay returns the stored response. Consumed events are de-duplicated by `event_id` (inbox table).
- **Crash safety**: every transition is committed before the next call; on restart the service resumes unfinished sagas from the state store (Temporal or Camunda may be used as the engine underneath; the contract above is unchanged).
- **DLQ**: poison messages on consumed topics are parked in `onboarding.commands.DLQ` after 5 failed attempts and alerted.

## Notifications Triggered
The orchestrator is the **sole publisher** of customer-facing status events (topic `onboarding.status`), which the Notification Service consumes:

| Event | When | Customer message intent |
|---|---|---|
| `OnboardingStarted` | Case persisted | "We've received your application" |
| `OnboardingStepCompleted` | Each validation / provisioning step done | (Silent by default; used by app progress UI) |
| `OnboardingInReview` | Any validation returned `REVIEW` or a non-critical dependency degraded | "Your application is under review, we'll notify you within 24–72 h" |
| `OnboardingStepFailed` | Hard rejection or timeout at a step | "We couldn't complete your application because …" (reason code mapped to a safe message) |
| `OnboardingCompleted` | Card issued | "Welcome! Your wallet, IBAN and virtual card are ready" |
| `OnboardingFailed` | After compensation | Final failure notice + next steps |
