# Account Provisioning Service

## Responsibility
Creates the customer's **wallet account** (provisioning step 6b): generates the internal account number and the **IBAN** (Pakistan format `PK` + 2 check digits + 4-char bank code + 16-digit account, per SBP IBAN standard), creates/links the **CIF (Customer Information File)** in the **Core Banking System**, opens the wallet account with the correct product code and tier limits, and records the mapping `digitalId ↔ CIF ↔ account ↔ IBAN`. It is the only service allowed to talk to the Core Banking System's customer/account-opening interfaces.

## APIs Exposed
Sync (REST/gRPC via mesh; caller = Onboarding Orchestrator):

| Method | Path | Request | Response |
|---|---|---|---|
| `POST` | `/v1/accounts` | `{requestRef, caseId, digitalId, cnicHash, customerProfile (name, dob, address, msisdn), productCode, tier, kycVerificationId}` | `201 {accountId, accountNumber, iban, cifId, status: ACTIVE, openedAt}` — idempotent by `requestRef` |
| `POST` | `/v1/accounts/{accountId}/close` | `{requestRef, reason: ONBOARDING_ROLLBACK}` | **Compensation**: closes the account in CBS (zero balance) and marks it `CLOSED` |
| `GET` | `/v1/accounts/{accountId}` | — | Account details (masked) |
| `GET` | `/v1/accounts/by-digital-id/{digitalId}` | — | Lookup used by Card Management |

Async — **published** (topic `provisioning.events`, via outbox): `AccountProvisioned{caseId, digitalId, accountId, iban (masked), cifId}`, `AccountClosed{caseId, accountId, reason}`.
Async — **consumed**: `onboarding.status.OnboardingFailed` (defensive clean-up if a compensation call was lost), `cbs.events` (CDC feed from the Core Banking System for reconciliation).

## Dependencies
- **Upstream**: Onboarding Orchestrator.
- **Downstream**: **Core Banking System** (T24 / Finacle / Flexcube — internal system of record; ISO 8583 / SOAP / REST adapter over the internal network, mTLS); Digital Identity Service (`GET` identity for consistency checks).
- **Datastore**: Account DB (PostgreSQL, own schema) incl. IBAN sequence/reservation tables.
- **Event bus**: Kafka producer `provisioning.events`, consumer `onboarding.status`, `cbs.events`.

## Data Owned
- `account` (accountId, digitalId, cifId, accountNumber, iban, productCode, tier, status, openedAt, closedAt).
- `iban_reservation` (pre-generated/reserved account numbers to keep IBAN generation deterministic and collision-free; check-digit computed here).
- `cif_reference` (digitalId → CBS CIF id; the CIF *content* is owned by CBS).
- `provisioning_request` (requestRef, caseId, cbsRequestRef, status, attempts) — idempotency and reconciliation ledger.

The CBS remains the ledger of record for balances and postings; this service owns the account *provisioning facts* and the IBAN/account-number allocation.

## Resilience & Failure Handling
- **Circuit Breaker** on the CBS adapter (Resilience4j; also Envoy outlier detection). CBS batch windows (EOD) are handled by a scheduled "degraded" flag rather than by tripping the breaker.
- **Retry with exponential backoff**: 3 attempts, base 500 ms, ×2, jitter, on timeout/5xx. **Every CBS call carries a client-generated `cbsRequestRef`** so a retried `openAccount` cannot create duplicate CIFs/accounts (CBS interface must be idempotent on that ref; otherwise a `lookup-before-create` guard is used).
- **Timeout**: 10 s per CBS call; step budget 15 s.
- **Bulkhead**: dedicated connection pool to CBS bounded to the CBS contractual concurrency; onboarding traffic is isolated from other CBS consumers by using its own gateway/route.
- **Graceful degradation**: account creation is **critical — fails closed**. Two controlled degradations exist:
  - During CBS EOD/maintenance windows, the service returns `202 PENDING` with a reserved account number/IBAN; the orchestrator keeps the case in `PROVISIONING`, the customer is told "your account is being finalised", and a scheduler completes the CBS call when the window closes. Card issuance waits until the account is `ACTIVE`.
  - If CBS is unreachable beyond the saga deadline, the reservation is released and the orchestrator triggers compensation (revoke digital identity) → `OnboardingFailed{CORE_BANKING_UNAVAILABLE}`.
- **Compensation**: `close` is idempotent; if CBS rejects a close (e.g. non-zero balance because a promo credit landed), the request is parked in `provisioning_request.status = COMPENSATION_STUCK` and alerted for ops.
- **Reconciliation**: nightly job compares `account` with the CBS CDC feed; divergences alert.
- **Idempotency**: `requestRef` unique; replay returns the stored account.
- **DLQ**: `onboarding.status.account.DLQ`, `cbs.events.account.DLQ`.

## Notifications Triggered
Publishes `AccountProvisioned` (domain event). The Notification Service uses it to include the IBAN (masked) in the final "Welcome" message once `OnboardingCompleted` arrives, and Fraud/Analytics consume it for features. Customer-visible *status* updates (`OnboardingStepCompleted{ACCOUNT_PROVISIONING}`, `OnboardingInReview{ACCOUNT_PENDING_CBS}`) are published by the orchestrator.
