"""
Generates the e-wallet customer onboarding architecture diagram as SVG.

Run:  python diagram/generate_diagram.py
Out:  diagram/onboarding-architecture.svg  (rasterise to PNG with a headless
      browser, e.g. chrome --headless=new --screenshot=... file:///...svg)

The layout is hand-positioned so that swimlanes, numbered steps, sync (solid)
vs async (dashed) arrows and resilience badges stay legible and deterministic.
"""
from __future__ import annotations

import html
from pathlib import Path

W, H = 2500, 2330
FONT = "Segoe UI, Helvetica, Arial, sans-serif"

# ---------------------------------------------------------------- styles ---
STYLES = {
    "client":   dict(fill="#FFF8DC", stroke="#B7791F"),
    "infra":    dict(fill="#EDE9FE", stroke="#6B46C1"),
    "service":  dict(fill="#DCEBFA", stroke="#2B6CB0"),
    "core":     dict(fill="#E8E8E8", stroke="#4A5568"),
    "external": dict(fill="#FDEBD0", stroke="#C05621"),
    "db":       dict(fill="#E6F4EA", stroke="#2F855A"),
    "cache":    dict(fill="#FDE2E2", stroke="#C53030"),
    "side":     dict(fill="#FFFFFF", stroke="#718096"),
    "dlq":      dict(fill="#FEEBC8", stroke="#9C4221"),
}
SYNC = "#2D3748"
ASYNC = "#B7791F"

out: list[str] = []


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def text(x, y, lines, size=12, anchor="middle", weight="normal", fill="#1A202C",
         lh=None, style=""):
    if isinstance(lines, str):
        lines = [lines]
    lh = lh or size + 4
    out.append(
        f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" '
        f'font-weight="{weight}" fill="{fill}" font-family="{esc(FONT)}" {style}>'
    )
    for i, ln in enumerate(lines):
        dy = 0 if i == 0 else lh
        out.append(f'<tspan x="{x}" dy="{dy}">{esc(ln)}</tspan>')
    out.append("</text>")


def rect(x, y, w, h, style, rx=8, dashed=False, extra=""):
    s = STYLES[style]
    dash = ' stroke-dasharray="9,5"' if dashed else ""
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
        f'fill="{s["fill"]}" stroke="{s["stroke"]}" stroke-width="2"{dash} {extra}/>'
    )


def box(x, y, w, h, title, lines=(), style="service", title_size=14,
        line_size=11.5, dashed=False, double=False):
    rect(x, y, w, h, style, dashed=dashed)
    if double:
        s = STYLES[style]
        out.append(
            f'<rect x="{x+4}" y="{y+4}" width="{w-8}" height="{h-8}" rx="5" '
            f'fill="none" stroke="{s["stroke"]}" stroke-width="1.2"/>'
        )
    cx = x + w / 2
    ty = y + 20
    text(cx, ty, title, size=title_size, weight="bold")
    if lines:
        text(cx, ty + 19, list(lines), size=line_size, fill="#2D3748")


def cylinder(x, y, w, h, title, lines=(), style="db"):
    s = STYLES[style]
    ry = 9
    out.append(
        f'<path d="M{x},{y+ry} A{w/2},{ry} 0 0 1 {x+w},{y+ry} L{x+w},{y+h-ry} '
        f'A{w/2},{ry} 0 0 1 {x},{y+h-ry} Z" fill="{s["fill"]}" stroke="{s["stroke"]}" stroke-width="2"/>'
    )
    out.append(
        f'<path d="M{x},{y+ry} A{w/2},{ry} 0 0 0 {x+w},{y+ry}" fill="none" '
        f'stroke="{s["stroke"]}" stroke-width="2"/>'
    )
    cx = x + w / 2
    text(cx, y + ry + 20, title, size=11.5, weight="bold")
    if lines:
        text(cx, y + ry + 36, list(lines), size=10, fill="#2D3748", lh=12.5)


def arrow(points, dashed=False, label=None, label_pos=None, label_size=11,
          label_anchor="middle", stroke_width=2.2, head=True):
    color = ASYNC if dashed else SYNC
    d = "M" + " L".join(f"{px},{py}" for px, py in points)
    dash = ' stroke-dasharray="10,6"' if dashed else ""
    marker = ' marker-end="url(#arrow-async)"' if dashed else ' marker-end="url(#arrow-sync)"'
    if not head:
        marker = ""
    out.append(
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{stroke_width}"{dash}{marker}/>'
    )
    if label:
        lx, ly = label_pos
        text(lx, ly, label, size=label_size, anchor=label_anchor,
             fill=color, style='font-style="italic"')


def step(x, y, n, r=14):
    out.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="#1A202C" stroke="#FFFFFF" stroke-width="2"/>')
    text(x, y + 5, str(n), size=13, weight="bold", fill="#FFFFFF")


def badge(x, y, label, fill="#FFFFFF", stroke="#C53030", color="#C53030"):
    w = 7.2 * len(label) + 14
    out.append(
        f'<rect x="{x - w/2}" y="{y - 10}" width="{w}" height="20" rx="10" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
    )
    text(x, y + 4, label, size=11, weight="bold", fill=color)


def lane(y0, h, title, shade):
    out.append(
        f'<rect x="40" y="{y0}" width="1820" height="{h}" fill="{shade}" stroke="#CBD5E0" stroke-width="1"/>'
    )
    out.append(f'<rect x="40" y="{y0}" width="85" height="{h}" fill="#2D3748"/>')
    cx, cy = 82, y0 + h / 2
    text(cx, cy + 5, title, size=14, weight="bold", fill="#FFFFFF",
         style=f'transform="rotate(-90 {cx} {cy})"')


# ------------------------------------------------------------------ canvas ---
out.append(
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
)
out.append(
    f"""<defs>
  <marker id="arrow-sync" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="userSpaceOnUse">
    <path d="M0,0 L10,5 L0,10 Z" fill="{SYNC}"/>
  </marker>
  <marker id="arrow-async" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="userSpaceOnUse">
    <path d="M0,0 L10,5 L0,10 Z" fill="{ASYNC}"/>
  </marker>
</defs>"""
)
out.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>')

text(1250, 48, "E-Wallet Customer Onboarding — Microservices Architecture", size=28, weight="bold")
text(1250, 78,
     "Orchestration-based Saga · Event-driven notifications · Kubernetes + Istio service mesh · "
     "Solid arrows = synchronous REST/gRPC · Dashed arrows = asynchronous Kafka events",
     size=14, fill="#4A5568")

# ------------------------------------------------------------------- lanes ---
LANES = [
    (110, 150, "CLIENT"),
    (270, 200, "EDGE"),
    (480, 250, "ORCHESTRATION"),
    (740, 290, "INTERNAL VALIDATION"),
    (1040, 360, "EXTERNAL INTEGRATION"),
    (1410, 290, "PROVISIONING"),
    (1710, 180, "CORE SYSTEMS"),
    (1900, 270, "NOTIFICATION / EVENT BUS"),
]
for i, (y0, h, title) in enumerate(LANES):
    lane(y0, h, title, "#FAFAFA" if i % 2 == 0 else "#F1F5F9")

# ------------------------------------------------------------- event bus ---
BUS_X, BUS_W = 1700, 110
BUS_Y0, BUS_Y1 = 500, 2150
rect(BUS_X, BUS_Y0, BUS_W, BUS_Y1 - BUS_Y0, "infra", rx=10)
bcx, bcy = BUS_X + BUS_W / 2, (BUS_Y0 + BUS_Y1) / 2
text(bcx - 22, bcy, "EVENT BUS — Apache Kafka (3+ brokers, RF=3, acks=all) · Schema Registry (Avro)",
     size=15, weight="bold", fill="#44337A", style=f'transform="rotate(-90 {bcx-22} {bcy})"')
text(bcx + 2, bcy,
     "Topics: onboarding.status · onboarding.commands · compliance.review · provisioning.events · notifications",
     size=12, fill="#44337A", style=f'transform="rotate(-90 {bcx+2} {bcy})"')
text(bcx + 22, bcy,
     "Transactional outbox + CDC (Debezium) · consumer groups per service · DLQ per topic · retention 7d",
     size=12, fill="#44337A", style=f'transform="rotate(-90 {bcx+22} {bcy})"')

# ------------------------------------------------------------ 1. clients ---
box(560, 150, 280, 80, "Mobile App (iOS / Android)",
    ["Onboarding wizard · document & selfie capture",
     "OIDC (PKCE) login · sends Idempotency-Key"], style="client")
box(1000, 150, 280, 80, "Web App (SPA)",
    ["Same onboarding API contract",
     "OIDC (PKCE) · sends Idempotency-Key"], style="client")
step(530, 190, 1)
text(1260, 250, "HTTPS / TLS 1.3 · JSON over REST", size=11, anchor="end", fill="#4A5568")

# --------------------------------------------------------------- 2. edge ---
box(400, 320, 360, 100, "WAF + L7 Load Balancer",
    ["NGINX / Envoy / AWS ALB", "TLS termination · DDoS & OWASP WAF rules",
     "Health-check based routing to gateway pods"], style="infra")
box(900, 300, 560, 140, "API Gateway (Kong / Apigee / AWS API Gateway)",
    ["Single entry point · OAuth2/OIDC JWT validation (JWKS)",
     "Rate limiting (per client / per CNIC) · JSON-schema request validation",
     "Routing · Idempotency-Key required on POST /onboarding",
     "Injects X-Correlation-ID + W3C trace-context · PII field masking in access logs"],
    style="infra")
box(1520, 320, 160, 100, "Identity Provider",
    ["Keycloak / Auth0", "OAuth2 / OIDC", "issues customer JWT"], style="infra", title_size=13)

# clients -> LB
arrow([(700, 230), (700, 290), (580, 290), (580, 320)])
arrow([(1140, 230), (1140, 290), (580, 290)], head=False)
# LB -> GW
arrow([(760, 370), (900, 370)])
step(830, 350, 2)
# GW <-> IdP
arrow([(1460, 370), (1520, 370)])
text(1490, 352, ["JWKS /", "introspect"], size=9.5, fill=SYNC, lh=11, style='font-style="italic"')

# ------------------------------------------------------- 3. orchestrator ---
box(700, 515, 560, 170, "Onboarding Orchestrator Service  (Saga Orchestrator)",
    ["Owns the onboarding state machine:",
     "RECEIVED → VALIDATING → IN_REVIEW → PROVISIONING → COMPLETED / FAILED",
     "Fans out internal + external validations in parallel; runs provisioning steps sequentially",
     "Compensating transactions on failure (revoke card → close account → revoke digital ID)",
     "Idempotency-Key → cached result (safe client retries) · per-step timeouts & deadlines",
     "Persists every step transition (resumable after crash) · publishes status events via outbox"],
    style="service", line_size=11)
cylinder(430, 545, 190, 115, "Saga State Store (PostgreSQL)",
         ["onboarding_case · saga_step", "idempotency_key · outbox"])
cylinder(1340, 520, 210, 100, "Redis Cache (cluster)",
         ["in-progress onboarding session/state", "TTL 24h · idempotency short-circuit"], style="cache")
arrow([(700, 600), (620, 600)])
arrow([(1260, 570), (1340, 570)])

# GW -> orchestrator
arrow([(1180, 440), (1180, 480), (980, 480), (980, 520)])
step(1080, 480, 3)
badge(1180, 460, "IK · TO · RB")
text(1250, 464, "POST /v1/onboarding  (Idempotency-Key header)", size=11, anchor="start", fill=SYNC,
     style='font-style="italic"')

# orchestrator -> bus (async status events) and bus -> orchestrator (review outcomes)
arrow([(1260, 645), (BUS_X, 645)], dashed=True,
      label="publish onboarding.status: STARTED · IN_REVIEW · STEP_FAILED · COMPLETED",
      label_pos=(1480, 636), label_size=10.5)
arrow([(BUS_X, 672), (1260, 672)], dashed=True,
      label="consume compliance.review.* / fraud.review.* → resume saga after manual review",
      label_pos=(1480, 692), label_size=10.5)

# trunk for orchestrator's synchronous fan-out
TRUNK_X = 225
arrow([(760, 685), (760, 705), (TRUNK_X, 705), (TRUNK_X, 1432)], head=False, stroke_width=3)
text(770, 700, "sync fan-out (REST/gRPC via service mesh, mTLS)", size=10.5, anchor="start",
     fill=SYNC, style='font-style="italic"')

# ------------------------------------------------- 4. internal validation ---
FAN4 = 762
arrow([(TRUNK_X, FAN4), (1330, FAN4)], head=False, stroke_width=2)
badge(300, FAN4, "CB · RB · BH · TO")
text(430, FAN4 + 17, "step 4: parallel fan-out to internal validations (scatter-gather, 5 s deadline)",
     size=10.5, anchor="start", fill=SYNC, style='font-style="italic"')

box(250, 790, 300, 100, "Mobile Banking Validation Service",
    ["Validates MSISDN + CNIC wallet eligibility",
     "Checks existing wallet / duplicate customer",
     "Owns: eligibility_check, msisdn_registry"], style="service", title_size=13)
cylinder(270, 905, 150, 65, "Eligibility DB", ["PostgreSQL"])
box(580, 790, 200, 100, "Mobile Banking System",
    ["Existing wallet / MB", "platform (internal)", "SOAP/REST adapter"], style="core", title_size=12.5,
    double=True)
arrow([(550, 840), (580, 840)])
badge(565, 902, "CB · RB · FB")

box(830, 790, 300, 100, "SafeWatch Compliance Service",
    ["Sanctions / watchlist / PEP screening",
     "Fuzzy name match · manual review queue",
     "Owns: screening_result, watchlist_snapshot"], style="service", title_size=13)
cylinder(850, 905, 150, 65, "Screening DB", ["PostgreSQL + Elastic"])

box(1180, 790, 300, 100, "Fraud Management Service",
    ["Risk scoring: device, velocity, geo, ML model",
     "Decision: ALLOW / REVIEW / DENY",
     "Owns: risk_assessment, rules, features"], style="service", title_size=13)
cylinder(1200, 905, 150, 65, "Fraud DB + Feature Store", ["PostgreSQL · Redis"])

for cx_, n in ((400, "4a"), (980, "4b"), (1330, "4c")):
    arrow([(cx_, FAN4), (cx_, 790)])
    step(cx_ - 22, 776, n, r=13)

# async review-outcome events -> bus
arrow([(1100, 890), (1100, 1000), (BUS_X, 1000)], dashed=True)
arrow([(1450, 890), (1450, 1000)], dashed=True, head=False)
text(1130, 1018, "compliance.review.completed · fraud.review.completed  (manual-review outcomes resume the saga)",
     size=10.5, anchor="start", fill=ASYNC, style='font-style="italic"')

# ------------------------------------------------- 5. external integration ---
FAN5 = 1062
arrow([(TRUNK_X, FAN5), (970, FAN5)], head=False, stroke_width=2)
badge(300, FAN5, "CB · RB · BH · TO")
text(430, FAN5 + 17, "step 5: external verifications (parallel with step 4; KYC is mandatory, no fallback)",
     size=10.5, anchor="start", fill=SYNC, style='font-style="italic"')

box(250, 1090, 340, 100, "NADRA / KYC Integration Service",
    ["CNIC + biometric / document verification",
     "Normalises provider responses · caches verified KYC",
     "Owns: kyc_verification, document_hash"], style="service", title_size=13)
cylinder(605, 1105, 150, 70, "KYC DB (encrypted)", ["PostgreSQL · PII AES-256"])

box(800, 1090, 340, 100, "PMD Validation Service",
    ["CNIC ↔ MSISDN pair authentication (CMPA)",
     "Current-operator lookup (MNI) for ported numbers",
     "Owns: msisdn_ownership_check"], style="service", title_size=13)
cylinder(1155, 1105, 150, 70, "PMD Check DB", ["PostgreSQL"])

for cx_, n in ((420, "5a"), (970, "5b")):
    arrow([(cx_, FAN5), (cx_, 1090)])
    step(cx_ - 22, 1076, n, r=13)

# egress gateway
box(250, 1215, 1055, 55, "External Integration Gateway / Adapter Layer  (Envoy egress gateway + provider adapters)",
    ["Isolates third-party contracts: protocol/format translation · API key + mTLS to providers · "
     "per-provider rate limit, circuit breaker, bulkhead · request/response audit (masked)"],
    style="infra", title_size=13, line_size=10.5)
arrow([(420, 1190), (420, 1215)])
arrow([(970, 1190), (970, 1215)])
text(440, 1207, "REST/gRPC", size=9.5, anchor="start", fill=SYNC)
text(990, 1207, "REST/gRPC", size=9.5, anchor="start", fill=SYNC)

# external systems
box(250, 1295, 400, 85, "NADRA VeriSys / BioVeriSys  +  3rd-party KYC provider",
    ["National Database & Registration Authority (Pakistan): CNIC verification,",
     "fingerprint biometric match · KYC vendor: document OCR, liveness, face match"],
    style="external", title_size=12.5, dashed=True, line_size=10.5)
box(800, 1295, 400, 85, "PMD — Pakistan MNP Database (Guarantee) Ltd",
    ["Telco-owned MNP clearing house · CMPA service: CNIC–MSISDN pair authentication",
     "MNI: mobile network identification (post-porting operator lookup)"],
    style="external", title_size=12.5, dashed=True, line_size=10.5)
arrow([(450, 1270), (450, 1295)])
arrow([(1000, 1270), (1000, 1295)])
badge(560, 1283, "CB · RB · TO · mTLS/API-key")
badge(1110, 1283, "CB · RB · TO · mTLS/API-key")
text(1320, 1330, "EXTERNAL", size=12, weight="bold", anchor="start", fill="#C05621")
text(1320, 1348, "dependencies", size=11, anchor="start", fill="#C05621")
text(1320, 1366, "(dashed orange border)", size=10, anchor="start", fill="#C05621")

# ------------------------------------------------------ 6. provisioning ---
FAN6 = 1432
arrow([(TRUNK_X, FAN6), (1200, FAN6)], head=False, stroke_width=2)
badge(300, FAN6, "CB · RB · IK · TO")
text(1235, FAN6 + 4, "step 6: all validations passed → sequential provisioning 6a → 6b → 6c,",
     size=10.5, anchor="start", fill=SYNC, style='font-style="italic"')
text(1235, FAN6 + 18, "each step has a compensating action (Saga rollback in reverse order)",
     size=10.5, anchor="start", fill=SYNC, style='font-style="italic"')

box(250, 1470, 340, 100, "Digital Identity Service",
    ["Issues Digital Banking ID (DBID) · links CNIC,",
     "MSISDN, device binding · registers in IdP",
     "Owns: digital_identity, credential_binding"], style="service", title_size=13)
cylinder(270, 1585, 150, 65, "Identity DB", ["PostgreSQL"])

box(640, 1470, 340, 100, "Account Provisioning Service",
    ["Generates account no. + IBAN (PK IBAN scheme)",
     "Creates CIF + wallet account in Core Banking",
     "Owns: account, iban, cif_reference"], style="service", title_size=13)
cylinder(660, 1585, 150, 65, "Account DB", ["PostgreSQL"])

box(1030, 1470, 340, 100, "Card Management Service",
    ["Issues virtual debit card · card ↔ account link",
     "Stores only card token + last4 (PCI scope in CMS)",
     "Owns: card, card_token, card_status"], style="service", title_size=13)
cylinder(1050, 1585, 150, 65, "Card DB", ["PostgreSQL (tokenised)"])

for cx_, n in ((420, "6a"), (810, "6b"), (1200, "6c")):
    arrow([(cx_, FAN6), (cx_, 1470)])
    step(cx_ - 22, 1446, n, r=13)

# provisioning domain events -> bus
arrow([(560, 1570), (560, 1680), (BUS_X, 1680)], dashed=True)
arrow([(950, 1570), (950, 1680)], dashed=True, head=False)
arrow([(1340, 1570), (1340, 1680)], dashed=True, head=False)
text(1360, 1698, "provisioning.events: DigitalIdIssued · AccountProvisioned · CardIssued (transactional outbox)",
     size=10.5, anchor="end", fill=ASYNC, style='font-style="italic"')

# ------------------------------------------------------ 7. core systems ---
box(640, 1760, 340, 95, "Core Banking System (CBS)",
    ["T24 / Finacle / Flexcube — internal system of record",
     "CIF creation · account & IBAN registration · GL setup",
     "ISO 8583 / REST adapter · nightly recon"], style="core", title_size=13, double=True)
box(1030, 1760, 340, 95, "Card Management System (CMS)",
    ["Way4 / Euronet / TSYS — PCI-DSS zone (internal)",
     "Virtual card issuance · PAN vault · tokenisation",
     "3-D Secure enrolment · card controls"], style="core", title_size=13, double=True)
arrow([(880, 1570), (880, 1760)])
badge(880, 1735, "CB · RB · IK · TO")
text(895, 1720, "createCustomer / openAccount (idempotent by request-ref)", size=9.5, anchor="start", fill=SYNC)
arrow([(1270, 1570), (1270, 1760)])
badge(1270, 1735, "CB · RB · IK · TO")
text(1285, 1720, "issueVirtualCard (idempotent)", size=9.5, anchor="start", fill=SYNC)
text(250, 1795, "INTERNAL CORE SYSTEMS", size=12, weight="bold", anchor="start", fill="#4A5568")
text(250, 1813, "(grey, double border) — existing systems of record;", size=10.5, anchor="start", fill="#4A5568")
text(250, 1830, "accessed only via the owning provisioning service", size=10.5, anchor="start", fill="#4A5568")

# ----------------------------------------------------- 8. notification ---
box(600, 1945, 380, 150, "Notification Service",
    ["Consumer group 'notifications' on onboarding.status",
     "+ provisioning.events (at-least-once, dedupe by event_id)",
     "Renders templates (EN / UR) · routes by customer channel preference",
     "Per-channel bulkhead pools · retries with backoff per channel",
     "Failed deliveries → notifications.DLQ",
     "Owns: notification_template, preference, delivery_log"], style="service", line_size=10.5)
cylinder(400, 1965, 170, 80, "Notification DB", ["PostgreSQL", "templates · delivery_log"])
arrow([(600, 2005), (570, 2005)])

box(1080, 1940, 260, 48, "Push — FCM / APNs", style="external", title_size=12.5, dashed=True)
box(1080, 2000, 260, 48, "SMS Gateway (telco aggregator)", style="external", title_size=12.5, dashed=True)
box(1080, 2060, 260, 48, "Email — SES / SendGrid", style="external", title_size=12.5, dashed=True)
arrow([(980, 1964), (1080, 1964)])
arrow([(980, 2024), (1080, 2024)])
arrow([(980, 2084), (1080, 2084)])
badge(1030, 1950, "CB·RB·BH")
badge(1030, 2010, "CB·RB·BH")
badge(1030, 2070, "CB·RB·BH")

box(1380, 1950, 270, 70, "Dead-Letter Queues (DLQ)",
    ["onboarding.status.DLQ · notifications.DLQ",
     "alert on depth > 0 · replay tool after fix"], style="dlq", title_size=12.5, line_size=10.5)
arrow([(BUS_X, 1985), (1650, 1985)], dashed=True)

arrow([(BUS_X, 2125), (790, 2125), (790, 2095)], dashed=True)
step(1450, 2085, 7)
text(1470, 2090, "consume every step's status event → notify customer", size=10.5, anchor="start", fill=ASYNC,
     style='font-style="italic"')
text(1470, 2108, "STARTED · IN_REVIEW · FAILED · COMPLETED via push / SMS / email", size=10.5, anchor="start",
     fill=ASYNC, style='font-style="italic"')
text(250, 1990, "Fully asynchronous:", size=11.5, weight="bold", anchor="start", fill="#4A5568")
text(250, 2007, "no service calls the", size=10.5, anchor="start", fill="#4A5568")
text(250, 2023, "Notification Service", size=10.5, anchor="start", fill="#4A5568")
text(250, 2039, "directly — it reacts to", size=10.5, anchor="start", fill="#4A5568")
text(250, 2055, "events on the bus.", size=10.5, anchor="start", fill="#4A5568")

# --------------------------------------------------------------- sidebar ---
SX, SW = 1895, 565


def side(y, h, title, lines):
    rect(SX, y, SW, h, "side", rx=6)
    text(SX + 14, y + 22, title, size=13.5, anchor="start", weight="bold", fill="#2D3748")
    text(SX + 14, y + 42, list(lines), size=11, anchor="start", fill="#2D3748", lh=15.5)


side(110, 150, "Runtime platform (cross-cutting)", [
    "• All services are Docker containers on Kubernetes (EKS / AKS / on-prem)",
    "• Namespace per bounded context · HPA autoscaling · PodDisruptionBudgets",
    "• Multi-AZ; stateful stores (PostgreSQL, Kafka, Redis) run HA with replication",
    "• Configuration via ConfigMaps; secrets via Vault CSI / External Secrets",
    "• Blue/green + canary rollouts through the mesh (traffic splitting)",
    "• Each lane is independently scalable — one Deployment per microservice",
])
side(280, 235, "Service mesh, discovery & service-to-service load balancing", [
    "• Istio (or Linkerd) sidecars on every pod → mTLS everywhere (SPIFFE identities)",
    "• Service Discovery: Kubernetes CoreDNS + mesh registry (Consul optional for VMs)",
    "• Envoy L7 load balancing for service-to-service calls (least-request, locality-aware)",
    "• Mesh-level outlier detection (circuit breaking), retries, timeouts, connection pools",
    "• Application-level Resilience4j for business fallbacks (fine-grained bulkheads)",
    "• AuthorizationPolicies: only the orchestrator may call validation/provisioning APIs",
  "• Egress gateway forces all third-party traffic (NADRA, PMD, SMS, Email) through one exit",
    "• Zero-trust: deny-all default NetworkPolicies, allow explicit lane-to-lane edges",
])
side(535, 285, "Observability", [
    "• Centralised logging: EFK — Fluent Bit → Elasticsearch → Kibana (structured JSON logs,",
    "  PII fields masked at source: CNIC, MSISDN, biometrics, PAN)",
    "• Distributed tracing: OpenTelemetry SDK + Collector → Jaeger; one trace per onboarding",
    "  request; X-Correlation-ID = onboarding_id propagated through HTTP headers and",
    "  Kafka message headers so async hops stay on the same trace",
    "• Metrics & alerting: Prometheus + Grafana + Alertmanager — RED/USE dashboards,",
    "  saga step latency, step failure ratio, circuit-breaker state, DLQ depth, Kafka",
    "  consumer lag, external-provider SLA (NADRA / PMD p95, error %)",
    "• SLOs: onboarding p95 < 90 s end-to-end (excl. manual review), success rate > 98 %",
    "• Business KPIs: funnel drop-off per step, manual-review queue age",
    "• Audit trail: immutable event log (Kafka → object storage) for regulator (SBP) reviews",
])
side(840, 300, "Security", [
    "• Client-facing auth: OAuth2 / OIDC (Authorization Code + PKCE) via Identity Provider;",
    "  JWT validated at API Gateway, re-validated in mesh (RequestAuthentication)",
    "• Service-to-service: mTLS via Istio, SPIFFE IDs, least-privilege AuthorizationPolicy",
    "• External integrations (NADRA, PMD): mTLS client certs + API keys, IP allow-listing,",
    "  credentials stored in HashiCorp Vault / cloud KMS, rotated automatically",
    "• PII encryption: TLS 1.2+ in transit; AES-256 at rest (disk + column-level envelope",
    "  encryption for CNIC, biometrics, documents); documents in object storage with SSE-KMS",
    "• Field-level masking / tokenisation of KYC data in logs, traces, non-prod copies",
    "• Card data never touches microservices — PAN stays in CMS (PCI-DSS scope isolation)",
    "• WAF + rate limiting at edge; secrets scanning + image signing (cosign) in CI",
    "• Data residency: all PII stores in-country as required by SBP regulations",
])
side(1160, 300, "Resilience policy (applied at every badge)", [
    "CB  Circuit Breaker — Resilience4j in-app + Envoy outlier detection; half-open probes",
    "RB  Retry with exponential backoff + jitter (3 attempts, only on idempotent operations,",
    "    never on 4xx business rejections)",
    "BH  Bulkhead — dedicated thread/connection pools per dependency; K8s resource limits",
    "TO  Timeouts per hop (validations 3 s, NADRA 8 s, CBS 10 s) with saga-level deadline",
    "IK  Idempotency-Key — client key at gateway; request-ref on CBS/CMS calls; event_id dedupe",
    "DLQ Poison / repeatedly failing events parked per topic; alert + replay after fix",
    "FB  Graceful degradation: if a NON-critical dependency is down (SafeWatch or Fraud)",
    "    the saga moves to IN_REVIEW and finishes asynchronously; Mobile Banking check falls",
    "    back to local eligibility cache. Critical steps (NADRA KYC, CBS, CMS) never degrade —",
    "    they fail closed and trigger compensation. Saga is resumable after any crash.",
])
side(1480, 200, "Data management", [
    "• Database-per-service: every microservice owns its schema; no shared DB, no cross-",
    "  service joins — data is shared only through APIs or published events",
    "• Transactional outbox in each service → Debezium CDC → Kafka (no dual-write problem)",
    "• Redis holds only ephemeral in-progress session/state (TTL); source of truth is the",
    "  Saga State Store. Read models for support/back-office built from events (CQRS)",
    "• Schema evolution via Avro Schema Registry (backward-compatible only)",
    "• Retention: KYC evidence 10 years (regulatory); Kafka 7 days; Redis 24 h",
])
side(1700, 170, "Footnote — CI/CD & delivery (not diagrammed in detail)", [
    "• GitHub Actions / GitLab CI per service: unit + contract tests (Pact), SAST/DAST,",
    "  dependency & container scanning, SBOM, image signing",
    "• Helm charts + Argo CD (GitOps) promote dev → staging → prod; canary via Istio",
    "• Consumer-driven contract tests protect the orchestrator ↔ service APIs",
    "• Chaos experiments (kill NADRA adapter, saturate Kafka) validate fallbacks",
    "• Environment parity: WireMock stubs for NADRA / PMD / CBS in non-prod",
])
side(1890, 280, "Step index (matches numbered circles)", [
    "1  Client submits customer details + documents",
    "2  L7 Load Balancer → API Gateway (auth, rate limit, validation, idempotency)",
    "3  API Gateway → Onboarding Orchestrator (saga starts, STARTED event)",
    "4  Parallel internal validations: 4a Mobile Banking · 4b SafeWatch · 4c Fraud",
    "5  Parallel external verifications via egress gateway: 5a NADRA/KYC · 5b PMD",
    "6  Provisioning (sequential, compensable): 6a Digital ID · 6b Account/IBAN in",
    "   Core Banking · 6c Virtual card in Card Management System",
    "7  Notification Service consumes every status event → push / SMS / email",
    "",
    "Any step failure → orchestrator runs compensations in reverse order,",
    "marks case FAILED and publishes STEP_FAILED (customer is informed).",
])

# ---------------------------------------------------------------- legend ---
LY = 2190
out.append(f'<rect x="40" y="{LY}" width="2420" height="110" fill="#FFFFFF" stroke="#CBD5E0"/>')
text(60, LY + 26, "LEGEND", size=14, anchor="start", weight="bold")

arrow([(150, LY + 22), (250, LY + 22)])
text(262, LY + 26, "Synchronous call (REST / gRPC, request-response)", size=11.5, anchor="start")
arrow([(150, LY + 52), (250, LY + 52)], dashed=True)
text(262, LY + 56, "Asynchronous event (Kafka topic, fire-and-forget, at-least-once)", size=11.5, anchor="start")
step(170, LY + 84, "n", r=12)
text(190, LY + 88, "Flow step number (matches functional flow)", size=11.5, anchor="start")

sw_x = 720
for i, (label, style, dashed, double) in enumerate([
    ("Client application", "client", False, False),
    ("Edge / platform infrastructure", "infra", False, False),
    ("Internal microservice (own DB)", "service", False, False),
    ("Internal core system of record", "core", False, True),
    ("External third-party dependency", "external", True, False),
]):
    yy = LY + 14 + i * 19
    rect(sw_x, yy, 34, 14, style, rx=3, dashed=dashed)
    if double:
        out.append(f'<rect x="{sw_x+3}" y="{yy+3}" width="28" height="8" fill="none" stroke="#4A5568" stroke-width="1"/>')
    text(sw_x + 44, yy + 11, label, size=11.5, anchor="start")

cylinder(1020, LY + 14, 60, 34, "", style="db")
text(1090, LY + 36, "Service-owned datastore (database-per-service)", size=11.5, anchor="start")
cylinder(1020, LY + 60, 60, 34, "", style="cache")
text(1090, LY + 82, "Cache (Redis) — ephemeral state only", size=11.5, anchor="start")

badge(1470, LY + 24, "CB · RB · BH · TO · IK")
text(1580, LY + 28, "Resilience patterns at that integration point:", size=11.5, anchor="start")
text(1400, LY + 56, "CB Circuit breaker · RB Retry w/ exponential backoff · BH Bulkhead isolation",
     size=11, anchor="start", fill="#4A5568")
text(1400, LY + 76, "TO Timeout · IK Idempotency key · FB Fallback / graceful degradation · DLQ Dead-letter queue",
     size=11, anchor="start", fill="#4A5568")
text(1400, LY + 96, "Trunk line from orchestrator = its synchronous fan-out (all via mesh mTLS, discovered by K8s DNS)",
     size=11, anchor="start", fill="#4A5568")

out.append("</svg>")

dst = Path(__file__).with_name("onboarding-architecture.svg")
dst.write_text("\n".join(out), encoding="utf-8")
print(f"wrote {dst} ({dst.stat().st_size/1024:.1f} KB)")
