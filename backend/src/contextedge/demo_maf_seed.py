"""Seed deterministic MAF demo graph data with clean, fully connected scenarios, historical episodes, and multi-source evidence items."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text

from contextedge.database import async_session_factory
from contextedge.graph.builder import ensure_edge
from contextedge.models.episode import Episode
from contextedge.models.evidence import EvidenceItem
from contextedge.models.pattern import Pattern, PatternEvidenceLink
from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.models.source import Source
from contextedge.models.tenant import Domain, Tenant, User
from contextedge.seed import resolve_owner_user, resolve_seed_tenant
from contextedge.seed_guard import require_destructive_reset_allowed

DEMO_NAMESPACE = uuid.UUID("778ddaf7-0b68-4c26-a7df-3b539ba7a72c")

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "key": "db-pool",
        "pattern_title": "Database server unresponsive / connection pool exhaustion on ORDERS_DB",
        "pattern_description": (
            "Customer Ordering Service becomes unavailable when a runaway reporting "
            "query holds ORDERS_DB connections, saturates the SQL connection pool, "
            "and cascades into CPU pressure and OrderApp health-check failures."
        ),
        "confidence": 0.88,
        "triggers": [
            "System.Data.SqlClient reports Connection Timeout Expired.",
            "ORDERS_DB pool utilization approaches Max Pool Size.",
            "Order submission and health checks fail at the same time.",
        ],
        "entities": ["Customer Ordering Service", "OrderApp v4.2", "APPPROD02", "ORDERS_DB", "SQLPROD01"],
        "errors": ["DT-4432: SQLPROD01 CPU saturation 96%", "SPL-99812: 2,841 SQL connection errors"],
        "root_causes": ["Runaway REPORT_MONTHLY_SALES query held 61 connections."],
        "resolution_steps": [
            "Capture active sessions and pool statistics.",
            "Kill the confirmed runaway reporting session.",
            "After emergency approval, restart MSSQLSERVER on SQLPROD01.",
            "Recycle the orders-prod IIS application pool on APPPROD02.",
        ],
        "playbook_key": "demo.supportflo.db_pool_recovery",
        "playbook_title": "Recover ORDERS_DB from connection-pool exhaustion",
        "playbook_description": "Recommendation-only recovery for ORDERS_DB connection pool saturation.",
        "risk_tier": "high",
        "episodes": [
            {
                "incident": "INC0010427",
                "date": "2026-07-27T10:15:00Z",
                "title": "Incident Analysis INC0010427 - Runaway sales report connection leak",
                "summary": "Monthly sales report query held 61 SQL connections causing OrderApp timeouts.",
            },
            {
                "incident": "INC0009812",
                "date": "2026-06-14T14:30:00Z",
                "title": "Incident Analysis INC0009812 - Unindexed inventory batch query spike",
                "summary": "Unindexed batch update exhausted connection pool during peak afternoon checkout.",
            },
            {
                "incident": "INC0008431",
                "date": "2026-04-02T09:00:00Z",
                "title": "Incident Analysis INC0008431 - Connection leak in OrderApp v4.1 thread pool",
                "summary": "OrderApp thread pool deadlock failed to close unhandled SQL sockets.",
            },
            {
                "incident": "INC0007204",
                "date": "2026-01-19T18:45:00Z",
                "title": "Incident Analysis INC0007204 - Flash sale traffic surge saturated SQLPROD01",
                "summary": "High traffic volume during flash sale exceeded Max Pool Size limit.",
            },
            {
                "incident": "INC0006110",
                "date": "2025-11-28T03:20:00Z",
                "title": "Incident Analysis INC0006110 - DB backup job collided with checkout peak",
                "summary": "Automated night backup job acquired exclusive lock on ORDERS_DB tables.",
            },
            {
                "incident": "INC0005230",
                "date": "2025-09-05T11:10:00Z",
                "title": "Incident Analysis INC0005230 - Connection pool max size misconfiguration",
                "summary": "Max Pool Size parameter was restricted to 20 connections following deployment.",
            },
        ],
    },
    {
        "key": "sso-cert",
        "pattern_title": "Company-wide SSO logins failing from expired ADFS signing certificate",
        "pattern_description": (
            "Company-wide SSO fails when the ADFS token-signing certificate expires "
            "while automatic rollover is disabled, causing relying parties to reject SAML assertions."
        ),
        "confidence": 0.93,
        "triggers": [
            "ADFS emits MSIS7012 and Event 133.",
            "Multiple unrelated relying parties reject SAML assertions.",
        ],
        "entities": ["Enterprise SSO", "ADFS Farm sts.corp.local", "ADFS01", "ADFS02"],
        "errors": ["EVT-1200: token-signing certificate expired", "OKTA-77: SAML rejection"],
        "root_causes": ["ADFS token-signing certificate expired at 00:00 UTC."],
        "resolution_steps": [
            "Enable controlled break-glass access for critical applications.",
            "Generate and install a new signing certificate on ADFS nodes.",
            "Promote the certificate and refresh relying-party metadata.",
        ],
        "playbook_key": "demo.supportflo.adfs_certificate_rollover",
        "playbook_title": "Recover Enterprise SSO with an ADFS certificate rollover",
        "playbook_description": "Certificate rollover and federation metadata refresh for ADFS SSO.",
        "risk_tier": "high",
        "episodes": [
            {
                "incident": "INC0011052",
                "date": "2026-07-28T00:05:00Z",
                "title": "Incident Analysis INC0011052 - Primary token-signing cert expired at 00:00 UTC",
                "summary": "Token-signing certificate expired causing Okta and Salesforce SAML rejections.",
            },
            {
                "incident": "INC0009540",
                "date": "2026-05-20T08:40:00Z",
                "title": "Incident Analysis INC0009540 - Okta SAML metadata XML checksum mismatch",
                "summary": "Relying party federation metadata failed to auto-update after certificate renewal.",
            },
            {
                "incident": "INC0008120",
                "date": "2026-03-11T16:15:00Z",
                "title": "Incident Analysis INC0008120 - Decryption certificate expiration on ADFS02",
                "summary": "Secondary ADFS farm node rejected encrypted SAML tokens.",
            },
            {
                "incident": "INC0006900",
                "date": "2025-12-14T13:50:00Z",
                "title": "Incident Analysis INC0006900 - Auto-rollover scheduled task privilege failure",
                "summary": "Auto-rollover PowerShell task failed due to service account password change.",
            },
            {
                "incident": "INC0005780",
                "date": "2025-10-02T10:30:00Z",
                "title": "Incident Analysis INC0005780 - Intermediate CA certificate revoked in AD",
                "summary": "Intermediate Certificate Authority revocation caused chain validation failure.",
            },
            {
                "incident": "INC0004910",
                "date": "2025-07-18T07:15:00Z",
                "title": "Incident Analysis INC0004910 - STS perimeter proxy SSL cert expired",
                "summary": "Web Application Proxy SSL cert expired blocking external SSO traffic.",
            },
        ],
    },
    {
        "key": "disk-full",
        "pattern_title": "Shared drive write failure from file-server capacity exhaustion",
        "pattern_description": (
            "Finance users cannot save files to shared drives when FS-FIN01 volume F: "
            "is exhausted by stale VSS snapshots and a backup dump incorrectly written to data volume."
        ),
        "confidence": 0.86,
        "triggers": [
            "FINSHARE users receive a not-enough-space error.",
            "FS-FIN01 volume F: falls below 1% free space.",
        ],
        "entities": ["Finance File Services", "FINSHARE", "FS-FIN01", "Volume F:"],
        "errors": ["SCOM-8812: F: free space 0.4%", "VSS-ERR: snapshot creation failing"],
        "root_causes": ["Stale VSS snapshots consumed 610 GB and misdirected backup dump."],
        "resolution_steps": [
            "Analyze disk usage and delete VSS snapshots older than 14 days.",
            "Remove misdirected backup dump and retarget FIN-DAILY backup job.",
        ],
        "playbook_key": "demo.supportflo.finshare_capacity_recovery",
        "playbook_title": "Restore FINSHARE capacity safely",
        "playbook_description": "Clean snapshot storage and misdirected dumps on FS-FIN01.",
        "risk_tier": "medium",
        "episodes": [
            {
                "incident": "INC0011348",
                "date": "2026-07-26T15:20:00Z",
                "title": "Incident Analysis INC0011348 - Stale VSS snapshots & 380GB dump on F:\\",
                "summary": "Volume F: reached 99.6% capacity causing Excel save errors for Finance team.",
            },
            {
                "incident": "INC0010112",
                "date": "2026-06-08T11:45:00Z",
                "title": "Incident Analysis INC0010112 - Finance SQL dump written to share root",
                "summary": "Automated SQL backup script saved database dump directly to user share.",
            },
            {
                "incident": "INC0008961",
                "date": "2026-05-03T08:10:00Z",
                "title": "Incident Analysis INC0008961 - Shadow copy storage quota exceeded",
                "summary": "VSS shadow copy storage limit reached 100% preventing volume writes.",
            },
            {
                "incident": "INC0007540",
                "date": "2026-02-22T17:30:00Z",
                "title": "Incident Analysis INC0007540 - Uncompressed video archive stored in FINSHARE",
                "summary": "Marketing team uploaded 450 GB raw video assets to Finance share.",
            },
            {
                "incident": "INC0006320",
                "date": "2025-11-10T12:00:00Z",
                "title": "Incident Analysis INC0006320 - Audit log rotation failure on FS-FIN01",
                "summary": "Security auditing service failed to archive rotated event logs.",
            },
            {
                "incident": "INC0005100",
                "date": "2025-08-29T09:40:00Z",
                "title": "Incident Analysis INC0005100 - System volume shadow copy growth blocked writes",
                "summary": "Shadow copy growth consumed unallocated cluster sectors on FS-FIN01.",
            },
        ],
    },
    {
        "key": "hr-payroll",
        "pattern_title": "HR Portal Workday sync failure and payroll queue lock",
        "pattern_description": (
            "HR specialists cannot process new employee onboarding or monthly payroll when "
            "the Workday API integration OAuth token expires, locking the asynchronous queue."
        ),
        "confidence": 0.91,
        "triggers": [
            "HRIS Workday sync queue reports HTTP 401 Unauthorized.",
            "Payroll batch processor queue depth exceeds 500 items.",
        ],
        "entities": ["HR Portal Workday Integration", "HR-PROD-APP01", "Workday API Gateway"],
        "errors": ["HRIS-401: OAuth2 client secret expired", "QUEUE-503: Payroll queue deadlock"],
        "root_causes": ["Workday API service account client secret expired."],
        "resolution_steps": [
            "Rotate OAuth2 credentials in Key Vault for Workday API.",
            "Release stale queue lock on HR-PROD-APP01 and restart worker pool.",
        ],
        "playbook_key": "demo.supportflo.hr_payroll_sync_recovery",
        "playbook_title": "Restore HR Workday portal sync and payroll processing",
        "playbook_description": "OAuth token renewal and queue deadlock clearance for HRIS.",
        "risk_tier": "medium",
        "episodes": [
            {
                "incident": "INC0011980",
                "date": "2026-07-29T08:00:00Z",
                "title": "Incident Analysis INC0011980 - Workday API secret expired after 365 days",
                "summary": "OAuth client secret expired preventing monthly payroll batch execution.",
            },
            {
                "incident": "INC0010720",
                "date": "2026-07-02T14:15:00Z",
                "title": "Incident Analysis INC0010720 - Stale worker pid lock on HR-PROD-APP01",
                "summary": "Deadlocked worker process held exclusive mutex lock on payroll queue.",
            },
            {
                "incident": "INC0009330",
                "date": "2026-05-28T11:30:00Z",
                "title": "Incident Analysis INC0009330 - SAP SuccessFactors payroll schema mismatch",
                "summary": "New benefit code field in Workday API payload rejected by SAP sync worker.",
            },
            {
                "incident": "INC0008010",
                "date": "2026-03-19T09:20:00Z",
                "title": "Incident Analysis INC0008010 - Employee benefits batch sync timeout",
                "summary": "Network gateway timeout interrupted bulk employee benefits synchronization.",
            },
            {
                "incident": "INC0006740",
                "date": "2026-01-04T16:45:00Z",
                "title": "Incident Analysis INC0006740 - Year-end W-2 tax document queue congestion",
                "summary": "Year-end tax PDF generation job flooded asynchronous task queue.",
            },
            {
                "incident": "INC0005610",
                "date": "2025-10-15T13:10:00Z",
                "title": "Incident Analysis INC0005610 - New hire onboarding CSV delimiter error",
                "summary": "Malformed CSV file from HR onboarding portal crashed ingestion thread.",
            },
        ],
    },
)

def _demo_id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(DEMO_NAMESPACE, f"{kind}:{key}")

async def seed_maf_demo() -> None:
    require_destructive_reset_allowed("demo_maf_seed")
    async with async_session_factory() as db:
        from contextedge.tenant_rls import bind_session_tenant

        await bind_session_tenant(db, None, bypass=True)
        print("1. Truncating old unlinked data tables...")
        tables_to_wipe = [
            "playbook_evidence_links",
            "pattern_evidence_links",
            "graph_edges",
            "playbook_versions",
            "playbooks",
            "patterns",
            "episode_steps",
            "episodes",
            "evidence_chunks",
            "evidence_items",
            "contradictions",
            "negative_knowledge_items",
        ]
        for tbl in tables_to_wipe:
            try:
                await db.execute(text(f"TRUNCATE TABLE {tbl} CASCADE;"))
                await db.commit()
                print(f"  [OK] Truncated {tbl}")
            except Exception:
                await db.rollback()
                raise

        print("2. Fetching Tenant, Domain, and Admin User...")
        tenant = await resolve_seed_tenant(db)
        if tenant is None:
            raise RuntimeError("No seed tenant found. Run `python -m contextedge.seed` first.")
        domain = (
            await db.execute(
                select(Domain).where(
                    Domain.tenant_id == tenant.id,
                    Domain.name == "General IT Operations",
                )
            )
        ).scalar_one()
        owner = await resolve_owner_user(db, tenant.id)
        if owner is None:
            raise RuntimeError(
                "No user found in the seed tenant. Create users in Settings or set SEED_* env vars, then re-run seed."
            )

        demo_source = (
            await db.execute(select(Source).where(Source.tenant_id == tenant.id))
        ).scalars().first()
        if demo_source is None:
            demo_source = Source(
                tenant_id=tenant.id,
                display_name="SupportFlo Enterprise Connector",
                source_type="servicenow",
                owner_user_id=owner.id,
                auth_type="basic",
                config={},
            )
            db.add(demo_source)
            await db.flush()

        print("3. Seeding 4 clean patterns with multi-source evidence per episode...")
        for scenario in SCENARIOS:
            key = scenario["key"]
            pattern_id = _demo_id("pattern", key)
            playbook_id = _demo_id("playbook", key)
            version_id = _demo_id("playbook-version", key)

            # Pattern
            pattern = Pattern(
                id=pattern_id,
                tenant_id=tenant.id,
                domain_id=domain.id,
                title=scenario["pattern_title"],
                description=scenario["pattern_description"],
                pattern_type="recurring_issue",
                confidence=scenario["confidence"],
                episode_count=len(scenario["episodes"]),
                active_flag=True,
                contradiction_score=0.0,
                freshness_score=1.0,
                trigger_conditions=scenario["triggers"],
                core_entities=scenario["entities"],
                observed_errors=scenario["errors"],
                root_causes=scenario["root_causes"],
                resolution_steps=scenario["resolution_steps"],
                evidence_summary={
                    "source": "SupportFlo Enterprise Simulation",
                    "incident_count": len(scenario["episodes"]),
                },
            )
            db.add(pattern)

            # Playbook
            playbook = Playbook(
                id=playbook_id,
                tenant_id=tenant.id,
                domain_id=domain.id,
                owner_user_id=owner.id,
                stable_key=scenario["playbook_key"],
                title=scenario["playbook_title"],
                description=scenario["playbook_description"],
                lifecycle_state="approved",
                risk_tier=scenario["risk_tier"],
                automation_mode="suggest_only",
                approver_user_id=owner.id,
                current_version_id=version_id,
                last_validated_at=datetime.now(UTC),
                pattern_id=pattern_id,
            )
            db.add(playbook)

            # Playbook Version
            version = PlaybookVersion(
                id=version_id,
                tenant_id=tenant.id,
                playbook_id=playbook_id,
                semantic_version="1.0.0",
                trigger_conditions={"all": scenario["triggers"]},
                inputs=[{"name": "incident_id", "required": True}],
                outputs=[{"name": "verification_result"}],
                steps=[
                    {"order": index + 1, "instruction": step}
                    for index, step in enumerate(scenario["resolution_steps"])
                ],
                rollback_notes="Follow standard rollback and approval policy.",
                evidence_refs=[ep["incident"] for ep in scenario["episodes"]],
                playbook_confidence=scenario["confidence"],
                execution_confidence_guidance="Verify all prerequisite approvals.",
                verification_policy={"require_all_checks": True},
                published_at=datetime.now(UTC),
                published_by=owner.id,
            )
            db.add(version)
            await db.flush()

            # Playbook -> Pattern Edge
            await ensure_edge(
                db,
                tenant.id,
                "playbook",
                playbook_id,
                "pattern",
                pattern_id,
                "addresses",
                weight=scenario["confidence"],
                domain_id=domain.id,
                metadata={"label": scenario["playbook_title"]},
            )

            # Seed Episodes & MULTIPLE Evidence Items per Episode (ServiceNow + Splunk + Slack)
            for ep_data in scenario["episodes"]:
                ep_key = f"{key}-{ep_data['incident']}"
                episode_id = _demo_id("episode", ep_key)
                ep_dt = datetime.fromisoformat(ep_data["date"].replace("Z", "+00:00"))

                episode = Episode(
                    id=episode_id,
                    tenant_id=tenant.id,
                    domain_id=domain.id,
                    primary_case_ref=ep_data["incident"],
                    title=ep_data["title"],
                    status="completed",
                    extraction_confidence=scenario["confidence"],
                    root_cause_summary=ep_data["summary"],
                    final_outcome="Resolved following verified playbook steps",
                    created_at=ep_dt,
                    updated_at=ep_dt,
                )
                db.add(episode)

                # Pattern -> Episode Edge
                await ensure_edge(
                    db,
                    tenant.id,
                    "pattern",
                    pattern_id,
                    "episode",
                    episode_id,
                    "clusters",
                    weight=scenario["confidence"],
                    domain_id=domain.id,
                    metadata={"label": episode.title},
                )

                # 3 Multi-Source Evidence Items for THIS 1 Episode
                evidence_specs = [
                    {
                        "tag": "servicenow",
                        "type": "servicenow_incident",
                        "title": f"ServiceNow Incident {ep_data['incident']}",
                        "body": f"Ticket #{ep_data['incident']} filed by Monitoring Bot: {ep_data['summary']}",
                    },
                    {
                        "tag": "splunk",
                        "type": "splunk_log",
                        "title": f"Splunk Log Alert SPL-{ep_data['incident'][-5:]}",
                        "body": f"Splunk Log Stream Error: High latency and timeout threshold breached for {ep_data['incident']}.",
                    },
                    {
                        "tag": "slack",
                        "type": "slack_message",
                        "title": f"Slack Incident Channel #inc-{ep_data['incident'].lower()}",
                        "body": f"Slack War-Room Thread: Engineers confirmed root cause and executed recovery steps for {ep_data['incident']}.",
                    },
                ]

                for spec in evidence_specs:
                    ev_key = f"{ep_key}-{spec['tag']}"
                    evidence_id = _demo_id("evidence", ev_key)

                    evidence = EvidenceItem(
                        id=evidence_id,
                        tenant_id=tenant.id,
                        domain_id=domain.id,
                        source_id=demo_source.id,
                        evidence_type=spec["type"],
                        title=spec["title"],
                        body_text=spec["body"],
                        content_hash=f"hash_{ev_key}_clean",
                        ingested_at=ep_dt,
                    )
                    db.add(evidence)
                    await db.flush()

                    # Link PatternEvidenceLink
                    pel = PatternEvidenceLink(
                        tenant_id=tenant.id,
                        pattern_id=pattern_id,
                        episode_id=episode_id,
                        evidence_id=evidence_id,
                        link_type="derived_from",
                        weight=scenario["confidence"],
                    )
                    db.add(pel)

                    # Episode -> Evidence Edge
                    await ensure_edge(
                        db,
                        tenant.id,
                        "episode",
                        episode_id,
                        "evidence",
                        evidence_id,
                        "derived_from",
                        weight=1.0,
                        domain_id=domain.id,
                        metadata={"label": evidence.title},
                    )

            print(f"[OK] Seeded Pattern: {scenario['pattern_title']} (with multi-evidence per episode)")

        await db.commit()
        print("[SUCCESS] All episodes and multi-source evidence items cleanly seeded!")

if __name__ == "__main__":
    asyncio.run(seed_maf_demo())
