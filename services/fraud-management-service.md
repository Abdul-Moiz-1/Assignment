# Fraud Management Service

## Responsibility
Computes an **onboarding fraud risk score** for the applicant and returns a decision (`ALLOW`, `REVIEW`, `DENY`). It combines rule-based checks (velocity of applications per device/IP/CNIC, disposable-email/VoIP number patterns, geo-IP vs declared address, known-fraud device fingerprints, synthetic-identity heuristics) with an ML model score. It also owns the fraud-analyst review queue for `REVIEW` outcomes and continuously learns from confirmed-fraud labels.

## APIs Exposed
Sync (REST/gRPC via mesh; caller = Onboarding Orchestrator):

| Method | Path | Request | Response |
|---|---|---|---|
| `POST` | `/v1/risk-assessments` | `{requestRef, caseId, cnicHash, msisdn, email, deviceFingerprint, ipAddress, geo, declaredAddress, channel, appVersion}` | `{decision: ALLOW \| REVIEW \| DENY, riskScore (0-1000), reasonCodes[], modelVersion, assessmentId}` |
| `GET` | `/v1/risk-assessments/{assessmentId}` | — | Replay / detail (masked) |

Back-office (fraud-analyst role):
- `GET /v1/review-queue`, `POST /v1/review-queue/{assessmentId}/decision {decision: ALLOW|DENY, notes}`.
- `POST /v1/labels` — confirmed fraud / genuine labels for model retraining.

Async — **published**:
- Topic `fraud.review`: `FraudReviewRequired{caseId, assessmentId}`; `FraudReviewCompleted{caseId, assessmentId, decision}` (resumes the saga).
- Topic `fraud.signals`: `DeviceBlacklisted`, `VelocityThresholdBreached` (consumed by other risk consumers, e.g. transaction monitoring).

Async — **consumed**:
- `onboarding.status` (all events) — to compute velocity features (applications per device/CNIC per hour) and to close the feedback loop (completed vs failed cases).
- `provisioning.events` — to enrich the feature store with account/card issuance facts.

## Dependencies
- **Upstream**: Onboarding Orchestrator; fraud back-office UI.
- **Downstream**: internal model-serving endpoint (same bounded context, deployed as a sidecar/separate Deployment — e.g. Seldon/KServe); optional external device-intelligence provider via the External Integration Gateway (non-blocking enrichment).
- **Datastores**: Fraud DB (PostgreSQL — assessments, rules, review queue) and Feature Store (Redis for hot velocity counters; offline features in the analytics lake, not on the request path).
- **Event bus**: Kafka producer (`fraud.review`, `fraud.signals`), consumer (`onboarding.status`, `provisioning.events`).

## Data Owned
- `risk_assessment` (assessmentId, caseId, requestRef, decision, riskScore, reasonCodes, modelVersion, rulesVersion, createdAt).
- `rule` (versioned rule set with thresholds; hot-reloadable).
- `feature_snapshot` (the exact features used for a decision — explainability/audit).
- `device_fingerprint`, `velocity_counter` (Redis, TTL-based).
- `review_case` and `fraud_label`.

No raw documents or biometrics; PII fields are hashed or tokenised.

## Resilience & Failure Handling
- **Circuit Breaker** around the ML model-serving endpoint and around the optional external device-intelligence call (Resilience4j).
- **Retry with exponential backoff**: 1 retry for the model call (100 ms base); none for the enrichment call (best-effort).
- **Timeout**: 800 ms for model inference, 500 ms for enrichment; total step budget 3 s.
- **Bulkhead**: rule engine, model inference and enrichment each run on their own thread pool; the event consumer (feature updates) runs in a separate deployment so analytics load never affects online scoring.
- **Graceful degradation** (tiered):
  1. Enrichment provider down → score without those features, mark `reasonCodes += ENRICHMENT_UNAVAILABLE`.
  2. ML model unavailable → fall back to rules-only scoring; decision cannot be `ALLOW` above a conservative threshold, so borderline cases become `REVIEW`.
  3. Service itself unavailable → orchestrator moves case to `IN_REVIEW{DEPENDENCY_UNAVAILABLE}`; the service back-fills the assessment when it recovers and publishes `FraudReviewCompleted`.
  Fraud is **non-critical for latency** but no account is provisioned without an assessment on record.
- **Idempotency**: unique `requestRef`; replays return the stored assessment. Analyst decisions idempotent per `assessmentId`.
- **DLQ**: `onboarding.status.fraud.DLQ`, `provisioning.events.fraud.DLQ`; outbox pattern for published decisions.
- Model governance: every decision stores `modelVersion` + `feature_snapshot` for challenge/explainability; shadow-mode deployment for new models.

## Notifications Triggered
Indirect via the orchestrator: `FraudReviewRequired` → `OnboardingInReview{FRAUD_REVIEW}`; `FraudReviewCompleted{DENY}` or a synchronous `DENY` → `OnboardingStepFailed{FRAUD, reasonCode: RISK_DECLINED}` (customer receives a neutral decline). `ALLOW` is silent.
