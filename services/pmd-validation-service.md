# PMD Validation Service

## Responsibility
Validates the applicant's **mobile number ownership** against **PMD — Pakistan Mobile Number Portability Database (Guarantee) Limited**, the telco-owned, PTA-regulated Mobile Number Portability clearing house. PMD is connected to all four cellular mobile operators (Jazz, Telenor, Zong, Ufone) and, beyond MNP, operates centralised regulatory services used by banks and branchless-banking providers:

- **CMPA — CNIC–MSISDN Pair Authentication** (digital KYC): confirms that the SIM/MSISDN the customer is onboarding with is registered against the **same CNIC** the customer declared (SBP requirement that a wallet's mobile number belongs to the account holder).
- **MNI — Mobile Network Identification**: resolves the *current* operator of a (possibly ported) number so downstream SMS/USSD routing and operator-specific checks use the right MNO.
- **SIM count / status** signals (active vs blocked SIM) where exposed to the financial industry.

This service owns the business rule "is this MSISDN acceptable for this CNIC", caches results for their permitted validity window, and isolates the platform from PMD's proprietary protocol via the External Integration Gateway.

## APIs Exposed
Sync (REST/gRPC via mesh; caller = Onboarding Orchestrator):

| Method | Path | Request | Response |
|---|---|---|---|
| `POST` | `/v1/msisdn/validations` | `{requestRef, caseId, msisdn, cnic}` | `{result: MATCH \| MISMATCH \| NOT_FOUND \| INCONCLUSIVE, operator: {mnc, name}, simStatus?: ACTIVE \| BLOCKED, pairAuthRef, checkedAt, validUntil}` |
| `GET` | `/v1/msisdn/validations/{requestRef}` | — | Replay (idempotency) |
| `GET` | `/v1/msisdn/{msisdnHash}/operator` | — | Operator lookup only (used by Notification Service for SMS routing) |

Async — **published** (topic `pmd.events`, via outbox): `MsisdnOwnershipValidated{caseId, result, operator}` for audit and for the Mobile Banking Validation Service's registry.

## Dependencies
- **Upstream**: Onboarding Orchestrator; Notification Service (operator lookup, read-only).
- **Downstream (external, via External Integration Gateway)**: PMD CMPA and MNI endpoints — mTLS client certificate + API key issued under the bank's PMD service agreement; IP allow-listed; credentials in Vault.
- **Datastore**: PMD Check DB (PostgreSQL, own schema).
- **Event bus**: Kafka producer `pmd.events`.

## Data Owned
- `msisdn_ownership_check` (requestRef, caseId, msisdnHash, cnicHash, result, operatorMnc, simStatus, pairAuthRef, checkedAt, validUntil).
- `operator_lookup_cache` (msisdnHash → operator, TTL 24 h — numbers can port, so short-lived).
- PMD quota/usage counters (per-day call budget under the commercial agreement).

MSISDN/CNIC stored as HMAC hashes plus masked display forms; PMD's raw response is stored encrypted for dispute handling.

## Resilience & Failure Handling
- **Circuit Breaker** on the PMD adapter (Resilience4j + Envoy outlier detection in the egress gateway). Open → `503` to the orchestrator within milliseconds.
- **Retry with exponential backoff**: 2 retries, base 400 ms, jitter, on timeout/5xx only; PMD calls are billable, so the same `pairAuthRef`/request id is reused and retries are capped.
- **Timeout**: 5 s per PMD call (orchestrator step budget 6 s).
- **Bulkhead**: dedicated connection pool sized to PMD's contractual TPS; overflow rejected immediately (`429`) rather than queued.
- **Graceful degradation**: CNIC–MSISDN pairing is a **regulatory (critical) check → fails closed**. Permitted mitigations only:
  - Reuse a cached `MATCH` for the same CNIC+MSISDN if still within `validUntil`.
  - If only the MNI (operator lookup) part is down, ownership validation still proceeds; operator defaults to prefix-based inference flagged `INFERRED`.
  - If CMPA is unavailable, the orchestrator holds the step with retries until the saga deadline and then fails the case with `MSISDN_VERIFICATION_UNAVAILABLE`; the customer is invited to retry later. Optional fallback path (product decision): OTP-to-MSISDN proves possession but **not** CNIC ownership, so it can only unlock a restricted lower tier.
- **Idempotency**: `requestRef` unique constraint; replays served from the DB.
- **Rate limiting** toward PMD aligned with the daily quota; alerts at 80 % consumption.
- **DLQ**: `pmd.events.DLQ` for outbox publication failures.

## Notifications Triggered
None directly. The orchestrator publishes `OnboardingStepCompleted{PMD_VALIDATION}` or `OnboardingStepFailed{PMD_VALIDATION, reasonCode}` with reason codes such as `MSISDN_NOT_REGISTERED_TO_CNIC`, `SIM_BLOCKED`, `MSISDN_VERIFICATION_UNAVAILABLE`, which the Notification Service renders for the customer ("The mobile number must be registered in your own name").
