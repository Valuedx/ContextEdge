"""Reset database tables and seed clean, fully-connected scenarios:
1. Database server unresponsive / connection pool exhaustion on ORDERS_DB
2. Company-wide SSO logins failing from expired ADFS signing certificate
3. Shared drive write failure on FINSHARE file server
4. HR Workday Portal sync failures and payroll data lock on HR-PROD
"""

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
        "incident": "INC0010427",
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
        "entities": [
            "Customer Ordering Service",
            "OrderApp v4.2",
            "APPPROD02",
            "ORDERS_DB",
            "SQLPROD01",
        ],
        "errors": [
            "DT-4432: SQLPROD01 CPU saturation 96%",
            "SPL-99812: 2,841 SQL connection errors in 15 minutes",
            "MON-441: OrderApp health checks 12/12 failing",
        ],
        "root_causes": [
            "Runaway REPORT_MONTHLY_SALES query held 61 connections.",
            "Connection-pool exhaustion caused CPU saturation and application timeouts.",
        ],
        "resolution_steps": [
            "Capture active sessions, blocking chains, and pool statistics.",
            "Kill the confirmed runaway reporting session.",
            "After emergency approval, restart MSSQLSERVER on SQLPROD01.",
            "Recycle the orders-prod IIS application pool on APPPROD02.",
            "Verify SQL latency, error volume, OrderApp health, and a test order.",
        ],
        "playbook_key": "demo.supportflo.db_pool_recovery",
        "playbook_title": "Recover ORDERS_DB from connection-pool exhaustion",
        "playbook_description": (
            "Recommendation-only recovery based on INC0009812: preserve diagnostics, "
            "terminate the confirmed blocker, gate the MSSQL restart behind emergency "
            "approval, recycle the OrderApp pool, and verify the service end to end."
        ),
        "risk_tier": "high",
    },
    {
        "key": "sso-cert",
        "incident": "INC0011052",
        "pattern_title": "Company-wide SSO logins failing from expired ADFS signing certificate",
        "pattern_description": (
            "Company-wide SSO fails when the ADFS token-signing certificate expires "
            "while automatic rollover is disabled, causing relying parties to reject "
            "SAML assertions."
        ),
        "confidence": 0.93,
        "triggers": [
            "ADFS emits MSIS7012 and Event 133.",
            "Multiple unrelated relying parties reject SAML assertions.",
            "The token-signing certificate is expired or near expiry.",
        ],
        "entities": [
            "Enterprise SSO",
            "ADFS Farm sts.corp.local",
            "ADFS01",
            "ADFS02",
            "Token-signing certificate",
        ],
        "errors": [
            "EVT-1200: token-signing certificate expired",
            "OKTA-77: 6/6 relying parties reject SAML assertions",
            "Login success rate fell to 3%",
        ],
        "root_causes": [
            "ADFS token-signing certificate expired at 00:00 UTC.",
            "Automatic rollover was disabled and the compensating renewal task was missing.",
        ],
        "resolution_steps": [
            "Enable controlled break-glass access for critical applications.",
            "Generate and install a new signing certificate on both ADFS nodes.",
            "After emergency approval, promote the certificate and restart nodes sequentially.",
            "Refresh federation metadata for all relying parties.",
            "Verify SAML assertions and login success across all applications.",
        ],
        "playbook_key": "demo.supportflo.adfs_certificate_rollover",
        "playbook_title": "Recover Enterprise SSO with an ADFS certificate rollover",
        "playbook_description": (
            "Recommendation-only recovery based on INC0006540: activate break-glass "
            "access, install a new certificate, gate promotion and service restart "
            "behind approval, refresh relying-party metadata, and run SAML checks."
        ),
        "risk_tier": "high",
    },
    {
        "key": "disk-full",
        "incident": "INC0011348",
        "pattern_title": "Shared drive write failure from file-server capacity exhaustion",
        "pattern_description": (
            "Finance users cannot save files to shared drives when FS-FIN01 volume F: "
            "is exhausted by stale VSS snapshots and a backup dump incorrectly written to the data volume."
        ),
        "confidence": 0.86,
        "triggers": [
            "FINSHARE users receive a not-enough-space error.",
            "FS-FIN01 volume F: falls below the free-space threshold.",
            "VSS snapshot creation fails from insufficient capacity.",
        ],
        "entities": [
            "Finance File Services",
            "FINSHARE",
            "FS-FIN01",
            "Volume F:",
            "BKP-VAULT",
        ],
        "errors": [
            "SCOM-8812: F: free space 0.4%",
            "SCOM-8790: free space below 10% for nine days",
            "VSS-ERR: snapshot creation failing",
        ],
        "root_causes": [
            "Stale VSS snapshots consumed approximately 610 GB.",
            "FIN-DAILY wrote a 380 GB backup dump to F:\\backup_tmp.",
        ],
        "resolution_steps": [
            "Analyze disk usage and confirm the growth sources.",
            "Delete VSS snapshots older than 14 days.",
            "After approval and checksum verification, remove the misdirected backup dump.",
            "Retarget FIN-DAILY to BKP-VAULT and add a free-space guard.",
            "Verify free space, VSS health, backup target, and a Finance file-save test.",
        ],
        "playbook_key": "demo.supportflo.finshare_capacity_recovery",
        "playbook_title": "Restore FINSHARE capacity safely",
        "playbook_description": (
            "Recommendation-only recovery based on INC0010112 and INC0009661: "
            "remove expendable snapshots first, require approval and backup validation "
            "before deleting the dump, retarget the backup job, and verify user writes."
        ),
        "risk_tier": "medium",
    },
    {
        "key": "hr-payroll",
        "incident": "INC0011980",
        "pattern_title": "HR Portal Workday sync failure and payroll queue lock",
        "pattern_description": (
            "HR specialists cannot process new employee onboarding or monthly payroll when "
            "the Workday API integration OAuth token expires, locking the asynchronous queue "
            "on HR-PROD-APP01 and delaying employee benefits sync."
        ),
        "confidence": 0.91,
        "triggers": [
            "HRIS Workday sync queue reports HTTP 401 Unauthorized.",
            "Payroll batch processor queue depth exceeds 500 pending items.",
            "Employee onboarding status updates stuck in PENDING_SYNC.",
        ],
        "entities": [
            "HR Portal Workday Integration",
            "HR-PROD-APP01",
            "Workday API Gateway",
            "Payroll Queue Service",
            "Employee Directory",
        ],
        "errors": [
            "HRIS-401: OAuth2 client credentials expired for Workday API",
            "QUEUE-503: Payroll processor lock acquired by stale worker pid 18402",
            "SYNC-99: 1,420 onboarding records waiting for sync",
        ],
        "root_causes": [
            "Workday API service account client secret expired after 365 days.",
            "Stale worker process held the payroll queue lock without renewing its token.",
        ],
        "resolution_steps": [
            "Rotate and update the OAuth2 credentials in Key Vault for Workday API.",
            "Release stale queue lock pid 18402 on HR-PROD-APP01.",
            "Flush and restart the HRIS Payroll Batch Worker pool.",
            "Trigger manual incremental sync for stuck onboarding records.",
            "Verify payroll queue depth, token validity, and test record sync.",
        ],
        "playbook_key": "demo.supportflo.hr_payroll_sync_recovery",
        "playbook_title": "Restore HR Workday portal sync and payroll processing",
        "playbook_description": (
            "Recovery guide for HRIS integration failures: renew OAuth credentials, "
            "release deadlocks on HR-PROD-APP01, restart worker threads, and trigger "
            "payroll batch processing."
        ),
        "risk_tier": "medium",
    },
)

def _demo_id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(DEMO_NAMESPACE, f"{kind}:{key}")

async def reset_and_seed():
    require_destructive_reset_allowed("reset_db_and_seed")
    async with async_session_factory() as db:
        print("1. Wiping all old pattern, playbook, episode, and evidence data...")
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

        print("3. Seeding clean scenarios with full GraphEdge connections...")
        for scenario in SCENARIOS:
            key = scenario["key"]
            pattern_id = _demo_id("pattern", key)
            playbook_id = _demo_id("playbook", key)
            version_id = _demo_id("playbook-version", key)
            episode_id = _demo_id("episode", key)
            evidence_id = _demo_id("evidence", key)

            # A. Pattern
            pattern = Pattern(
                id=pattern_id,
                tenant_id=tenant.id,
                domain_id=domain.id,
                title=scenario["pattern_title"],
                description=scenario["pattern_description"],
                pattern_type="recurring_issue",
                confidence=scenario["confidence"],
                episode_count=2,
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
                    "incident": scenario["incident"],
                },
            )
            db.add(pattern)

            # B. Playbook
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

            # C. Playbook Version
            version = PlaybookVersion(
                id=version_id,
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
                evidence_refs=[scenario["incident"]],
                playbook_confidence=scenario["confidence"],
                execution_confidence_guidance="Verify all prerequisite approvals.",
                verification_policy={"require_all_checks": True},
                published_at=datetime.now(UTC),
                published_by=owner.id,
            )
            db.add(version)

            # D. Episode
            episode = Episode(
                id=episode_id,
                tenant_id=tenant.id,
                domain_id=domain.id,
                primary_case_ref=scenario["incident"],
                title=f"Incident Analysis for {scenario['incident']}",
                status="completed",
                extraction_confidence=scenario["confidence"],
                root_cause_summary=" ".join(scenario["root_causes"]),
                final_outcome="Resolved following verified playbook steps",
            )
            db.add(episode)

            # E. Evidence Item
            evidence = EvidenceItem(
                id=evidence_id,
                tenant_id=tenant.id,
                domain_id=domain.id,
                source_id=demo_source.id,
                evidence_type="servicenow_incident",
                title=f"Raw Incident {scenario['incident']}",
                body_text=scenario["pattern_description"],
                content_hash=f"hash_{key}_clean_hash",
            )
            db.add(evidence)

            await db.flush()

            # F. PatternEvidenceLink
            pel = PatternEvidenceLink(
                pattern_id=pattern_id,
                episode_id=episode_id,
                evidence_id=evidence_id,
                link_type="clusters",
                weight=scenario["confidence"],
            )
            db.add(pel)

            # G. Graph Edges:
            # 1. Playbook -> Pattern (addresses)
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

            # 2. Pattern -> Episode (clusters)
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

            # 3. Episode -> Evidence (derived_from)
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

            print(f"  [OK] Seeded: {scenario['pattern_title']}")

        await db.commit()
        print("\nAll database tables reset and cleanly seeded with 100% connected graph edges!")

if __name__ == "__main__":
    asyncio.run(reset_and_seed())
