# Card Management Service

## Responsibility
Issues the customer's **virtual debit card** (provisioning step 6c) by requesting card creation in the **Card Management System (CMS)**, links the card to the newly provisioned wallet account, applies default card controls (e-commerce enabled, international disabled, tier-based limits), enrols the card for 3-D Secure, and stores a **card token** for the platform to reference the card without ever handling the PAN. It is the only microservice permitted to talk to the CMS, which keeps the PCI-DSS scope confined to the CMS and this service's outbound adapter.

## APIs Exposed
Sync (REST/gRPC via mesh; caller = Onboarding Orchestrator):

| Method | Path | Request | Response |
|---|---|---|---|
| `POST` | `/v1/cards` | `{requestRef, caseId, digitalId, accountId, cardProduct: VIRTUAL_DEBIT, nameOnCard, tier}` | `201 {cardId, cardToken, last4, expiry (MM/YY), status: ACTIVE, controls}` — idempotent by `requestRef` |
| `POST` | `/v1/cards/{cardId}/revoke` | `{requestRef, reason: ONBOARDING_ROLLBACK}` | **Compensation**: permanently blocks the card in CMS, status `REVOKED` |
| `GET` | `/v1/cards/{cardId}` | — | Card metadata (token, last4, status, controls) |
| `POST` | `/v1/cards/{cardId}/reveal-session` | — | Creates a short-lived session for the app to fetch full card details **directly from the CMS PCI zone** (PAN never transits this service) |

Async — **published** (topic `provisioning.events`, via outbox): `CardIssued{caseId, digitalId, cardId, last4}`, `CardRevoked{caseId, cardId, reason}`.
Async — **consumed**: `provisioning.events.AccountProvisioned` (pre-warms the account link), `provisioning.events.AccountClosed` (auto-revoke any card on a closed account), `onboarding.status.OnboardingFailed` (defensive clean-up).

## Dependencies
- **Upstream**: Onboarding Orchestrator; mobile/web app (only for `reveal-session`, via API Gateway).
- **Downstream**: **Card Management System** (Way4 / Euronet / TSYS — internal PCI-DSS zone; ISO 8583 / REST adapter, mTLS, network-segmented); Account Provisioning Service (`GET` account to confirm `ACTIVE` before issuance).
- **Datastore**: Card DB (PostgreSQL, own schema; **tokenised** — no PAN, no CVV).
- **Event bus**: Kafka producer/consumer on `provisioning.events`, consumer on `onboarding.status`.

## Data Owned
- `card` (cardId, digitalId, accountId, cardToken, last4, expiry, product, status ACTIVE/BLOCKED/REVOKED, issuedAt).
- `card_control` (cardId, ecommerceEnabled, internationalEnabled, dailyLimit, perTxnLimit).
- `card_issuance_request` (requestRef, caseId, cmsRequestRef, status, attempts) — idempotency/reconciliation ledger.
- 3-DS enrolment status.

The CMS owns PAN, CVV, PIN, authorisation and the card lifecycle; this service owns the platform-side card facts and token mapping.

## Resilience & Failure Handling
- **Circuit Breaker** on the CMS adapter (Resilience4j + Envoy outlier detection).
- **Retry with exponential backoff**: 3 attempts, base 500 ms, jitter, on timeout/5xx only. Each CMS call carries `cmsRequestRef` so a retried `issueVirtualCard` never mints two cards; if the CMS API is not idempotent, a `lookup-by-reference` guard runs before each retry.
- **Timeout**: 10 s per CMS call; step budget 15 s.
- **Bulkhead**: dedicated, bounded connection pool to the CMS; `reveal-session` traffic uses a separate pool so customer-facing reads do not compete with issuance.
- **Graceful degradation**: card issuance is the **last** step, so it can degrade without blocking the customer's account:
  - If the CMS is unavailable, the service returns `202 PENDING`; the orchestrator completes onboarding as `COMPLETED_CARD_PENDING` (account and IBAN usable), the customer is told the card will be ready shortly, and a scheduler issues the card when the CMS recovers, then publishes `CardIssued`. This is an explicit product decision — a wallet without a card is still useful.
  - Hard CMS rejection (e.g. product not eligible) → `OnboardingStepFailed{CARD_ISSUANCE}` but **no rollback of the account** (soft-fail; card can be issued later via customer support).
- **Compensation**: `revoke` is idempotent and safe to repeat; used when an upstream compensation cascade requires it (e.g. account closed after card issuance).
- **Idempotency**: `requestRef` unique; replay returns the same card.
- **Security / PCI**: no PAN/CVV persisted or logged; full details are only ever rendered via the CMS's PCI-compliant reveal endpoint; card token is CMS-issued and non-reversible.
- **DLQ**: `provisioning.events.card.DLQ`, `onboarding.status.card.DLQ`.

## Notifications Triggered
Publishes `CardIssued` (domain event). The Notification Service sends "Your virtual debit card ending •••• 1234 is ready" (push + SMS) and it also enriches the final `OnboardingCompleted` message. When issuance is deferred, the orchestrator publishes `OnboardingCompleted{cardStatus: PENDING}` and the later `CardIssued` triggers the follow-up notification.
