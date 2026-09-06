# Notification Service

## Responsibility
Keeps the customer informed at **every onboarding step** — started, in review, failed, completed — over push, SMS and email. It is purely **event-driven**: no service ever calls it synchronously. It subscribes to the onboarding status and provisioning topics on Kafka, maps each event to a template (English / Urdu), applies the customer's channel preferences and quiet-hours rules, renders the message with masked data only, and delivers it through external channel providers with per-channel isolation. It also keeps the delivery log for support and audit.

## APIs Exposed
Async — **consumed** (consumer group `notifications`, at-least-once, dedupe by `event_id`):

| Topic | Events | Resulting message |
|---|---|---|
| `onboarding.status` | `OnboardingStarted` | "Application received" (push + SMS) |
| | `OnboardingInReview{reason}` | "Under review, we'll update you within N h" |
| | `OnboardingStepFailed{step, reasonCode}` | Reason-code → safe customer text (never discloses compliance/fraud detail) |
| | `OnboardingCompleted{accountRef, cardStatus}` | "Welcome — wallet, IBAN and card ready" (push + SMS + email with masked IBAN) |
| | `OnboardingFailed{reasonCode}` | Final decline / retry instructions |
| | `OnboardingStepCompleted{step}` | Silent by default (app progress); configurable |
| `provisioning.events` | `CardIssued` (when card was deferred) | "Your card ending •••• 1234 is ready" |
| `notifications.commands` | `SendNotification{templateId, digitalId, params}` | Generic command used by back-office / other domains |

Async — **published** (topic `notifications.events`): `NotificationSent{eventId, channel, providerRef}`, `NotificationFailed{eventId, channel, reason}` (analytics, support timeline).

Sync (small, read-only; via API Gateway, customer scope): `GET /v1/notifications/preferences`, `PUT /v1/notifications/preferences`, `GET /v1/notifications/history` (delivery log for the logged-in customer). Internal: `GET /health/*`.

## Dependencies
- **Upstream (events)**: Onboarding Orchestrator (`onboarding.status`), provisioning services (`provisioning.events`), back-office (`notifications.commands`).
- **Downstream (external, via External Integration Gateway)**: Push — FCM / APNs; SMS — telco aggregator (with operator routing from PMD's MNI lookup via the PMD Validation Service, read-only); Email — Amazon SES / SendGrid.
- **Digital Identity Service** (`GET` identity → device push tokens, contact details) — cached locally with short TTL.
- **Datastore**: Notification DB (PostgreSQL, own schema).
- **Event bus**: Kafka consumer/producer; DLQ topics `notifications.DLQ`, `onboarding.status.notifications.DLQ`.

## Data Owned
- `notification_template` (templateId, locale, channel, version, body with placeholders; approved by compliance/marketing).
- `customer_preference` (digitalId, channels enabled, locale, quiet hours, marketing opt-in).
- `delivery_log` (notificationId, eventId, digitalId, channel, templateVersion, status QUEUED/SENT/DELIVERED/FAILED, providerRef, attempts, timestamps) — retained 2 years for support/audit; message *content* stored masked.
- `event_inbox` (processed `event_id`s for idempotency, TTL 7 days).
- Rate-limit / throttling counters per customer (e.g. max 10 SMS per day).

## Resilience & Failure Handling
- **Circuit Breaker** per channel provider (FCM, APNs, SMS aggregator, email provider) — an SMS aggregator outage does not affect push or email.
- **Retry with exponential backoff**: per delivery attempt, 4 retries (1 s → 2 s → 4 s → 8 s, jitter) on transient provider errors; scheduled retries persisted in `delivery_log` so they survive restarts. Provider `4xx` (invalid token/number) is not retried; the token is marked stale.
- **Timeout**: 3 s per provider call.
- **Bulkhead**: separate worker pools **and Kafka consumer instances** per channel; separate consumer group for `notifications.commands` so bulk campaigns cannot delay onboarding status messages (which have priority).
- **Graceful degradation / fallback**: channel fallback chain per event class — e.g. `OnboardingCompleted`: push → SMS if push not delivered within 60 s → email. If *all* channels for a customer fail, the message stays visible in the in-app notification centre (served from `delivery_log`) and a `NotificationFailed` event is published. Degraded PMD lookup → SMS routed via aggregator default routing.
- **Idempotency**: `event_id` inbox guarantees one notification per event even if Kafka redelivers; provider calls carry a client message id for de-duplication on their side.
- **Dead-letter queue**: after 5 consecutive processing failures (poison message, template missing, malformed event) the event goes to `notifications.DLQ`; Alertmanager pages on DLQ depth > 0; a replay tool re-injects after the fix. Consumer offsets are committed only after the delivery attempt is persisted (no message loss).
- **Ordering**: topics are partitioned by `caseId`/`digitalId`, so a customer's events are processed in order; a `COMPLETED` never overtakes a `STARTED`.
- **Back-pressure**: consumer lag metric drives HPA; if a provider throttles (`429`), the channel's workers pause using the provider's `Retry-After`.
- **Privacy**: templates receive only masked data (IBAN `PK36 SCBL **** **** **** 1234`, card last4); no CNIC/biometric data ever leaves the platform via notifications.

## Notifications Triggered
This service **sends** all customer notifications listed above and publishes `NotificationSent` / `NotificationFailed` for observability. It does not publish onboarding status events itself — it is strictly a consumer of them, which is what keeps the notification path asynchronous and decoupled from the saga's critical path.
