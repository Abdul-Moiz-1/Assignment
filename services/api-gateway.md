# API Gateway

## Responsibility
The API Gateway (Kong, Apigee or AWS API Gateway, fronted by a WAF + L7 load balancer) is the **single entry point** for the mobile and web apps into the onboarding platform. It is a policy-enforcement layer, not a business service: it terminates client TLS, authenticates the caller (OAuth2/OIDC JWT issued by the Identity Provider), validates and rate-limits requests, enforces the `Idempotency-Key` contract, injects correlation/trace headers, and routes traffic to the Onboarding Orchestrator (and to read-only status endpoints). No onboarding business logic or customer data lives here.

## APIs Exposed
Client-facing (sync, REST/JSON over HTTPS, all under `/v1`):

| Method | Path | Purpose | Notes |
|---|---|---|---|
| `POST` | `/v1/onboarding` | Start an onboarding case (customer details + document references) | `Idempotency-Key` header **required**; `Authorization: Bearer <JWT>`; body validated against JSON schema |
| `POST` | `/v1/onboarding/{caseId}/documents` | Upload CNIC images / selfie (pre-signed object-storage URL flow) | Multipart or pre-signed PUT; max 10 MB per file |
| `GET` | `/v1/onboarding/{caseId}` | Poll current status / step results | Served by orchestrator read model, 2 s cache at gateway |
| `GET` | `/v1/onboarding/{caseId}/events` | Server-sent events stream of status updates | Optional; falls back to polling |
| `POST` | `/v1/onboarding/{caseId}/resume` | Customer resumes an `IN_REVIEW`/`ACTION_REQUIRED` case with corrected data | Idempotent |

Gateway policies applied to every route: JWT validation via JWKS, scope check (`onboarding:write` / `onboarding:read`), per-client and per-CNIC rate limits (e.g. 5 `POST /onboarding` per hour per CNIC), request-size limits, schema validation, CORS, response PII masking on error payloads.

Headers injected downstream: `X-Correlation-ID` (= caseId once known), W3C `traceparent`, `X-Client-Id`, `X-Forwarded-For`.

## Dependencies
- **Upstream**: WAF + L7 Load Balancer (NGINX / Envoy / AWS ALB) → gateway pods.
- **Downstream**: Onboarding Orchestrator Service (REST/gRPC through the Istio mesh, mTLS).
- **Identity Provider** (Keycloak / Auth0): JWKS endpoint for signature verification; optional token introspection for revoked tokens.
- **Redis** (gateway-local cluster): rate-limit counters, idempotency-key short-term dedupe, response cache.
- **Observability**: emits access logs (PII-masked) to EFK, spans to the OpenTelemetry Collector, metrics to Prometheus.

## Data Owned
None of the customer domain. The gateway owns only its own operational data:
- Route/plugin configuration (declarative, in Git → applied via CI/CD).
- Rate-limit counters and `Idempotency-Key → caseId` short-lived map (Redis, TTL 24 h).
- API consumer registrations / client credentials metadata.

## Resilience & Failure Handling
- **Circuit Breaker** towards the orchestrator (Envoy outlier detection: eject pod after 5 consecutive 5xx for 30 s). When open → fast `503` with `Retry-After`, never queues requests.
- **Retry with exponential backoff**: only for idempotent verbs (`GET`) and for `POST /onboarding` *because* it carries an `Idempotency-Key`; max 2 retries, base 200 ms, jitter, total budget 3 s.
- **Timeouts**: 5 s per upstream call; 30 s for document upload route.
- **Bulkhead**: separate upstream connection pools per route group (write vs read vs upload) so a slow document upload cannot starve status polling.
- **Idempotency**: rejects `POST /onboarding` without `Idempotency-Key` (`400`); duplicate key with same payload → returns the original `202` + `caseId`; duplicate key with different payload → `409`.
- **Graceful degradation**: if the Identity Provider's JWKS endpoint is unreachable, cached signing keys are used (cache TTL 24 h) so authentication keeps working; if the orchestrator is down, `GET` status is served from the gateway cache (stale-while-revalidate, marked `X-Cache: STALE`).
- **Rate limiting / load shedding**: protects downstream during traffic spikes; `429` with `Retry-After`.
- HA: ≥ 3 replicas across availability zones behind the L7 LB; HPA on request rate.

## Notifications Triggered
None directly. The gateway never publishes to the event bus; status events are emitted by the Onboarding Orchestrator once the request is accepted.
