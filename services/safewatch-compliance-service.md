# SafeWatch Compliance Service

## Responsibility
Screens every applicant against **sanctions, watchlists and PEP (politically exposed persons) lists** — UN, OFAC, EU, UK HMT, Pakistan's NACTA proscribed-persons list and the bank's internal blacklist — using fuzzy name/DoB/CNIC matching. Produces a `CLEAR`, `REVIEW` (potential match needing a compliance officer) or `BLOCK` (true match) outcome, maintains the manual-review queue for compliance analysts, and keeps an auditable record of every screening for AML/CFT regulators (SBP, FMU).

## APIs Exposed
Sync (REST/gRPC via mesh; caller = Onboarding Orchestrator):

| Method | Path | Request | Response |
|---|---|---|---|
| `POST` | `/v1/screenings` | `{requestRef, caseId, fullName, fatherName, dob, cnic, nationality, addresses[]}` | `{result: CLEAR \| REVIEW \| BLOCK, matchScore, hits[] (list, score, maskedName), screeningId}` |
| `GET` | `/v1/screenings/{screeningId}` | — | Screening detail (masked) / replay |

Back-office (separate ingress, compliance-officer role via OIDC):
- `GET /v1/review-queue`, `POST /v1/review-queue/{screeningId}/decision {decision: CLEAR|BLOCK, notes}`.

Async — **published**:
- Topic `compliance.review`: `ComplianceReviewRequired{caseId, screeningId}` when result is `REVIEW`; `ComplianceReviewCompleted{caseId, screeningId, decision}` when an analyst decides — this event **resumes the orchestrator saga**.
- Topic `compliance.audit`: immutable `ScreeningPerformed` record for the audit trail.

Async — **consumed**:
- `watchlist.updates` (internal feed from list-ingestion job) to refresh the local list snapshot; triggers **re-screening** of recently onboarded customers (ongoing monitoring, outside the onboarding saga).

## Dependencies
- **Upstream**: Onboarding Orchestrator; compliance back-office UI.
- **Downstream**: none synchronous outside the service. Watchlist data is ingested in batch from list providers (Dow Jones / Refinitiv World-Check or direct regulator files) by an internal ingestion job — never on the request path.
- **Datastore**: Screening DB (PostgreSQL for screening records and review queue) + Elasticsearch index for fuzzy matching (both owned by this service only).
- **Event bus**: Kafka producer (`compliance.review`, `compliance.audit`), consumer (`watchlist.updates`).

## Data Owned
- `screening_result` (screeningId, caseId, requestRef, result, matchScore, algorithmVersion, listSnapshotVersion, createdAt).
- `screening_hit` (screeningId, listId, entryId, score, matchedFields).
- `review_case` (screeningId, assignedTo, status, decision, notes, decidedAt).
- `watchlist_snapshot` (versioned copies of the lists used — needed to reproduce a past decision).
- Matching configuration (thresholds per list, name-transliteration rules for Urdu/English variants).

## Resilience & Failure Handling
- **Circuit Breaker** around the Elasticsearch matching cluster; if open, the service returns `503` quickly so the orchestrator's own breaker/degradation policy applies.
- **Retry with exponential backoff** on transient Elasticsearch / DB errors (2 retries, 100 ms base).
- **Timeout**: 2 s for the match query; orchestrator budget is 3 s.
- **Bulkhead**: online screening and batch re-screening run in separate deployments (same code, different `mode`) so batch jobs can never starve onboarding traffic.
- **Graceful degradation**: this is a **non-critical-for-latency but mandatory-for-compliance** check. If screening cannot be performed synchronously, the orchestrator moves the case to `IN_REVIEW` (never approves); the service records a `PendingScreening` job and executes it as soon as it recovers, publishing `ComplianceReviewCompleted` (or `ComplianceReviewRequired`) to resume the saga. Customers are never onboarded without a completed screening.
- **Idempotency**: `requestRef` unique constraint → duplicate calls return the original screening; analyst decisions are idempotent per `screeningId`.
- **DLQ**: `watchlist.updates.DLQ` for malformed list deltas; events published via transactional outbox so a `REVIEW` decision is never lost.
- Deterministic decisions: each result stores the list snapshot version and algorithm version to satisfy audit reproducibility.

## Notifications Triggered
Indirect only. `ComplianceReviewRequired` causes the orchestrator to publish `OnboardingInReview{reason: COMPLIANCE_REVIEW}` → customer receives "under review". `ComplianceReviewCompleted{BLOCK}` causes `OnboardingStepFailed{COMPLIANCE, reasonCode: COMPLIANCE_DECLINED}` (customer gets a neutral decline message — the actual hit is never disclosed, per AML tipping-off rules). `CLEAR` lets the saga proceed silently.
