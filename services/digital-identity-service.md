# Digital Identity Service

## Responsibility
Issues and manages the customer's **Digital Banking ID (DBID)** — the platform-wide, immutable identifier that links the verified natural person (CNIC, KYC verification) to their wallet, devices and credentials. It is the first provisioning step (6a): once all validations pass, the orchestrator asks this service to create the identity; the resulting `digitalId` is then used by Account Provisioning and Card Management as the customer reference. The service also registers the customer in the Identity Provider (Keycloak/Auth0) so the customer can log in with their own credentials after onboarding, and binds the onboarding device (device fingerprint + push token) to the identity.

## APIs Exposed
Sync (REST/gRPC via mesh; caller = Onboarding Orchestrator; IdP admin API outbound):

| Method | Path | Request | Response |
|---|---|---|---|
| `POST` | `/v1/identities` | `{requestRef, caseId, cnicHash, kycVerificationId, fullName, dob, msisdn, email?, deviceBinding: {fingerprint, pushToken, platform}}` | `201 {digitalId, idpSubject, status: ACTIVE, createdAt}` — idempotent by `requestRef` |
| `DELETE` | `/v1/identities/{digitalId}` | `{requestRef, reason}` | **Compensation**: revokes identity (status `REVOKED`), disables IdP user, unbinds devices |
| `GET` | `/v1/identities/{digitalId}` | — | Identity profile (masked) |
| `GET` | `/v1/identities/by-cnic/{cnicHash}` | — | Existence check (one DBID per CNIC) |
| `POST` | `/v1/identities/{digitalId}/devices` | Add / rotate device binding | Used post-onboarding |

Async — **published** (topic `provisioning.events`, via outbox): `DigitalIdIssued{caseId, digitalId}`, `DigitalIdRevoked{caseId, digitalId, reason}`.
Async — **consumed**: `kyc.events.KycVerificationCompleted` (to link the verification evidence id; consistency check), `onboarding.status.OnboardingFailed` (defensive clean-up if a compensation call was lost).

## Dependencies
- **Upstream**: Onboarding Orchestrator.
- **Downstream**: Identity Provider admin API (Keycloak / Auth0) to create the user and set initial credentials/OTP enrolment; **KMS** for encrypting identity attributes.
- **Datastore**: Identity DB (PostgreSQL, own schema).
- **Event bus**: Kafka producer/consumer as above.

## Data Owned
Source of truth for the **customer's digital identity**:
- `digital_identity` (digitalId (UUIDv7), cnicHash, kycVerificationId, status ACTIVE/SUSPENDED/REVOKED, tier, createdAt).
- `identity_attribute` (encrypted: fullName, dob, msisdn, email; masked display columns).
- `credential_binding` (digitalId → idpSubject, MFA enrolment state).
- `device_binding` (digitalId, deviceFingerprintHash, pushToken (encrypted), platform, trustedAt).
- Uniqueness invariant: **one active DBID per CNIC**.

## Resilience & Failure Handling
- **Circuit Breaker** on the IdP admin API (Resilience4j).
- **Retry with exponential backoff**: 3 attempts, base 300 ms, jitter; IdP user creation is made idempotent by using `digitalId` as the IdP username/external id, so a retried create is a no-op.
- **Timeout**: 3 s for IdP calls; total step budget 5 s.
- **Bulkhead**: IdP calls isolated on their own pool; read APIs unaffected by IdP slowness.
- **Graceful degradation**: identity creation is **critical** (later steps depend on `digitalId`) but the IdP registration part is not on the critical path: if the IdP is down, the DBID is still issued and persisted with `credential_binding.status = PENDING`; a background reconciler completes IdP registration and the customer is told credentials will be ready shortly. If the DB itself is unavailable the step fails and the orchestrator retries; nothing downstream is provisioned.
- **Compensation**: `DELETE /v1/identities/{digitalId}` is idempotent and safe to call repeatedly; it marks the identity `REVOKED` (soft delete for audit) and disables the IdP user.
- **Idempotency**: `requestRef` unique; concurrent duplicate creates for the same CNIC are serialised by the uniqueness constraint (`409` → orchestrator reuses the existing DBID).
- **Consistency**: outbox pattern guarantees `DigitalIdIssued` is published exactly when the row commits.
- **DLQ**: `kyc.events.identity.DLQ`, `onboarding.status.identity.DLQ`.

## Notifications Triggered
Publishes the domain event `DigitalIdIssued` (consumed by the Notification Service to send a "Your digital banking ID is ready — set up your login" message once the whole onboarding completes, and by analytics). Customer-facing *status* events remain the orchestrator's (`OnboardingStepCompleted{DIGITAL_IDENTITY}`).
