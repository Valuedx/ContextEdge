"""Seed deterministic MAF demo graph data from the SupportFlo scenarios."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from contextedge.database import async_session_factory
from contextedge.graph.builder import ensure_edge
from contextedge.models.pattern import Pattern
from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.models.tenant import Domain, Tenant, User

DEMO_NAMESPACE = uuid.UUID("778ddaf7-0b68-4c26-a7df-3b539ba7a72c")

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "key": "db-pool",
        "incident": "INC0010427",
        "pattern_title": "Database connection pool exhaustion on ORDERS_DB",
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
        "pattern_title": "Enterprise SSO outage from expired ADFS signing certificate",
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
        "pattern_title": "FINSHARE write failures from file-server capacity exhaustion",
        "pattern_description": (
            "Finance users cannot save files when FS-FIN01 volume F: is exhausted by "
            "stale VSS snapshots and a backup dump incorrectly written to the data volume."
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
)


def _demo_id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(DEMO_NAMESPACE, f"{kind}:{key}")


async def seed_maf_demo() -> None:
    async with async_session_factory() as db:
        tenant = (await db.execute(select(Tenant).where(Tenant.slug == "default"))).scalar_one()
        domain = (
            await db.execute(
                select(Domain).where(
                    Domain.tenant_id == tenant.id,
                    Domain.name == "General IT Operations",
                )
            )
        ).scalar_one()
        owner = (
            await db.execute(
                select(User).where(
                    User.tenant_id == tenant.id,
                    User.email == "admin@contextedge.local",
                )
            )
        ).scalar_one()

        for scenario in SCENARIOS:
            key = scenario["key"]
            pattern_id = _demo_id("pattern", key)
            playbook_id = _demo_id("playbook", key)
            version_id = _demo_id("playbook-version", key)

            pattern = await db.get(Pattern, pattern_id)
            if pattern is None:
                pattern = Pattern(id=pattern_id, tenant_id=tenant.id)
                db.add(pattern)
            pattern.domain_id = domain.id
            pattern.title = scenario["pattern_title"]
            pattern.description = scenario["pattern_description"]
            pattern.pattern_type = "recurring_issue"
            pattern.confidence = scenario["confidence"]
            pattern.episode_count = 2
            pattern.active_flag = True
            pattern.contradiction_score = 0.0
            pattern.freshness_score = 1.0
            pattern.trigger_conditions = scenario["triggers"]
            pattern.core_entities = scenario["entities"]
            pattern.observed_errors = scenario["errors"]
            pattern.root_causes = scenario["root_causes"]
            pattern.resolution_steps = scenario["resolution_steps"]
            pattern.evidence_summary = {
                "source": "SupportFlo simulation",
                "incident": scenario["incident"],
            }

            playbook = await db.get(Playbook, playbook_id)
            if playbook is None:
                playbook = Playbook(
                    id=playbook_id,
                    tenant_id=tenant.id,
                    owner_user_id=owner.id,
                )
                db.add(playbook)
            playbook.domain_id = domain.id
            playbook.stable_key = scenario["playbook_key"]
            playbook.title = scenario["playbook_title"]
            playbook.description = scenario["playbook_description"]
            playbook.lifecycle_state = "approved"
            playbook.risk_tier = scenario["risk_tier"]
            playbook.automation_mode = "suggest_only"
            playbook.approver_user_id = owner.id
            playbook.current_version_id = version_id
            playbook.last_validated_at = datetime.now(UTC)
            playbook.expiry_at = None
            playbook.pattern_id = pattern_id

            version = await db.get(PlaybookVersion, version_id)
            if version is None:
                version = PlaybookVersion(
                    id=version_id,
                    playbook_id=playbook_id,
                    semantic_version="1.0.0",
                )
                db.add(version)
            version.trigger_conditions = {"all": scenario["triggers"]}
            version.branching_logic = {}
            version.inputs = [{"name": "incident_id", "required": True}]
            version.outputs = [{"name": "verification_result"}]
            version.steps = [
                {"order": index + 1, "instruction": step}
                for index, step in enumerate(scenario["resolution_steps"])
            ]
            version.rollback_notes = "Follow the per-step rollback and approval policy."
            version.evidence_refs = [scenario["incident"]]
            version.playbook_confidence = scenario["confidence"]
            version.execution_confidence_guidance = (
                "Suggestion only. Respect approval gates before disruptive or destructive actions."
            )
            version.verification_policy = {"require_all_checks": True}
            version.published_at = datetime.now(UTC)
            version.published_by = owner.id

            await db.flush()
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
            )

            print(f"{scenario['incident']} | pattern={pattern_id} | playbook={playbook_id}")

        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed_maf_demo())
