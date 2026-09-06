# External Integration Gateway / Adapter Layer

## Responsibility
A dedicated **egress layer** through which every call to a third party (NADRA, KYC vendor, PMD, SMS aggregator, push and email providers) must pass. It has two parts:

1. **Egress gateway** (Istio/Envoy egress gateway): single, allow-listed network exit; terminates the mesh's mTLS and originates provider-specific TLS/mTLS with certificates from Vault; enforces per-provider rate limits, timeouts, connection pools and outlier detection; produces a uniform audit trail of outbound calls.
2. **Provider adapters** (thin, stateless services or libraries deployed alongside the gateway): translate the platform's canonical requests into each provider's contract (SOAP/XML for NADRA VeriSys, proprietary JSON for PMD, REST for KYC vendors), handle provider authentication and signing, and map provider error codes to canonical outcomes.

Its purpose is **contract isolation**: if NADRA changes its API, only the NADRA adapter changes; the NADRA/KYC Integration Service and orchestrator are untouched.

## APIs Exposed
Internal canonical adapter APIs (sync, gRPC/REST, mesh mTLS; callers = the integration services listed below, enforced by `AuthorizationPolicy`):

| Adapter | Endpoint | Canonical request → provider call |
|---|---|---|
| NADRA | `POST /adapters/nadra/verisys/verify` | CNIC + demographics → VeriSys citizen verification |
| NADRA | `POST /adapters/nadra/bioverisys/match` | CNIC + fingerprint template → biometric match |
| KYC vendor | `POST /adapters/kyc/document-check` | Document refs + selfie → OCR / liveness / face-match |
| PMD | `POST /adapters/pmd/cmpa/authenticate` | CNIC + MSISDN → pair authentication |
| PMD | `GET /adapters/pmd/mni/{msisdn}` | MSISDN → current operator |
| Messaging | `POST /adapters/push/send`, `/adapters/sms/send`, `/adapters/email/send` | Canonical message → FCM/APNs / SMS aggregator / SES |

Every adapter response carries `providerRef`, `latencyMs`, `providerStatus` and a canonical `outcome`; raw provider payloads are returned only encrypted for evidence storage by the owning service.

No async APIs of its own; it emits `egress.audit` records (masked) to Kafka for the audit trail and metrics for provider SLA dashboards.

## Dependencies
- **Upstream (callers)**: NADRA/KYC Integration Service, PMD Validation Service, Notification Service, Fraud Management Service (optional enrichment).
- **Downstream (external)**: NADRA VeriSys / BioVeriSys, third-party KYC vendor, PMD (CMPA, MNI), FCM/APNs, SMS aggregator, SES/SendGrid.
- **Vault / KMS**: client certificates, API keys, signing keys (auto-rotated); **DNS/egress firewall**: only allow-listed provider hosts/IPs.
- **Observability**: OpenTelemetry spans (trace context propagated to providers where supported), Prometheus per-provider metrics, EFK audit logs.

## Data Owned
Stateless by design. Owns only:
- Provider configuration (endpoints, quotas, timeouts, certificate references, feature flags per provider) — in Git, applied by CI/CD.
- Rate-limit / quota counters (Redis, ephemeral).
- Outbound audit log stream (`egress.audit`, masked; the *evidence* copies of provider responses are owned by the calling service, e.g. `kyc_evidence`).

## Resilience & Failure Handling
This layer is where most external-facing resilience is enforced uniformly:
- **Circuit Breaker**: Envoy outlier detection per provider cluster (eject on 5 consecutive 5xx / connect failures, 30 s base ejection) in addition to the application-level breakers in the calling services.
- **Retry with exponential backoff**: Envoy retry policy limited to connection failures / `503` with `x-envoy-max-retries: 1`; business-level retries stay in the calling service to avoid retry storms (retry budgets capped at 20 % of traffic).
- **Timeouts**: per provider route (NADRA 8 s, PMD 5 s, messaging 3 s); idle and TCP connect timeouts tuned per provider.
- **Bulkhead**: per-provider connection pools and max-pending-request limits; a saturated NADRA pool returns `503` immediately and never affects PMD or messaging traffic. Separate gateway Deployments for **regulatory** (NADRA/PMD) vs **messaging** providers so a marketing SMS burst cannot impact KYC.
- **Rate limiting / quota protection**: token buckets aligned with each provider's contractual TPS and daily quota; alerts at 80 % consumption.
- **Graceful degradation**: none of its own — it reports provider health honestly (`503`/`429` with `Retry-After`) so that the *owning* service can apply its business fallback (e.g. cached PMD result, lower tier, `IN_REVIEW`).
- **Security**: mTLS + API keys to providers, IP allow-listing on both sides, request signing where required, PII masking in logs/traces, TLS certificate pinning for NADRA/PMD.
- **Change isolation**: provider contract versions are pinned; adapters have consumer-driven contract tests and WireMock stubs of each provider for non-prod environments.

## Notifications Triggered
None. It publishes only technical `egress.audit` records and provider SLA metrics; customer notifications are the Notification Service's responsibility.
