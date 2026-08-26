"""Phase 0 — read-only playbook data-readiness audit (MAF master plan).

Prints one JSON object of counts that decide whether Phase 4 (applicability
gate) and Phase 8 (feedback flywheel) are worth building, and sizes the
N1/N2/N3 blast radius.

    python -m scripts.playbook_data_readiness_audit

Requires a live Postgres. Sets ``app.bypass_rls`` so the scan is tenant-wide.
Nothing is written.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text

from contextedge.database import async_session_factory
from contextedge.tenant_rls import bind_session_tenant

TRIGGER_COVERAGE_GO = 0.30

_SQL = text(
    """
    WITH published AS (
      SELECT
        pv.id,
        pv.playbook_id,
        pv.trigger_conditions,
        pv.created_at,
        pv.published_at,
        CASE
          WHEN pv.trigger_conditions IS NULL THEN false
          WHEN jsonb_typeof(pv.trigger_conditions) = 'object'
            AND pv.trigger_conditions = '{}'::jsonb THEN false
          WHEN jsonb_typeof(pv.trigger_conditions) = 'array'
            AND jsonb_array_length(pv.trigger_conditions) = 0 THEN false
          ELSE true
        END AS has_triggers
      FROM playbook_versions pv
      WHERE pv.published_at IS NOT NULL
    ),
    latest_published AS (
      SELECT DISTINCT ON (playbook_id)
        playbook_id, id, created_at, has_triggers
      FROM published
      ORDER BY playbook_id, published_at DESC
    )
    SELECT jsonb_build_object(
      'playbooks_total', (SELECT count(*) FROM playbooks),
      'playbooks_approved', (
        SELECT count(*) FROM playbooks WHERE lifecycle_state = 'approved'
      ),
      'current_version_null', (
        SELECT count(*) FROM playbooks WHERE current_version_id IS NULL
      ),
      'approved_unpublished_current', (
        SELECT count(*)
        FROM playbooks p
        LEFT JOIN playbook_versions pv ON pv.id = p.current_version_id
        WHERE p.lifecycle_state = 'approved'
          AND p.current_version_id IS NOT NULL
          AND pv.published_at IS NULL
      ),
      'published_versions', (SELECT count(*) FROM published),
      'published_versions_with_triggers', (
        SELECT count(*) FROM published WHERE has_triggers
      ),
      'published_trigger_coverage', (
        SELECT CASE
          WHEN count(*) = 0 THEN NULL
          ELSE round(avg(has_triggers::int)::numeric, 4)
        END
        FROM published
      ),
      'playbooks_zero_evidence_links', (
        SELECT count(*)
        FROM playbooks p
        LEFT JOIN playbook_evidence_links pel
          ON pel.playbook_version_id IN (
            SELECT pv.id FROM playbook_versions pv WHERE pv.playbook_id = p.id
          )
        WHERE pel.id IS NULL
      ),
      'playbooks_with_embedding', (
        SELECT count(*) FROM playbooks WHERE embedding IS NOT NULL
      ),
      'playbooks_embedding_null', (
        SELECT count(*) FROM playbooks WHERE embedding IS NULL
      ),
      'approved_embedding_older_than_latest_published', (
        SELECT count(*)
        FROM playbooks p
        JOIN latest_published lp ON lp.playbook_id = p.id
        WHERE p.lifecycle_state = 'approved'
          AND p.embedding IS NOT NULL
          AND p.updated_at < lp.created_at
      ),
      'risk_tier_counts', (
        SELECT coalesce(jsonb_object_agg(tier, n), '{}'::jsonb)
        FROM (
          SELECT coalesce(risk_tier, '<null>') AS tier, count(*) AS n
          FROM playbooks
          GROUP BY risk_tier
          ORDER BY count(*) DESC
        ) s
      ),
      'negative_knowledge_items', (
        SELECT count(*) FROM negative_knowledge_items
      ),
      'negative_knowledge_with_evidence_refs', (
        SELECT count(*)
        FROM negative_knowledge_items
        WHERE evidence_refs IS NOT NULL
          AND jsonb_typeof(evidence_refs) = 'array'
          AND jsonb_array_length(evidence_refs) > 0
      ),
      'negative_knowledge_with_step_text', (
        SELECT count(*)
        FROM negative_knowledge_items
        WHERE step_text IS NOT NULL AND length(btrim(step_text)) > 0
      ),
      'retrieval_feedback_rows', (SELECT count(*) FROM retrieval_feedback),
      'retrieval_feedback_with_match_id', (
        SELECT count(*) FROM retrieval_feedback WHERE match_id IS NOT NULL
      ),
      'retrieval_feedback_with_playbook_id', (
        SELECT count(*) FROM retrieval_feedback WHERE playbook_id IS NOT NULL
      )
    ) AS audit
    """
)


def _gates(audit: dict) -> dict:
    coverage = audit.get("published_trigger_coverage")
    coverage_f = float(coverage) if coverage is not None else 0.0
    feedback = int(audit.get("retrieval_feedback_rows") or 0)
    return {
        "phase_4_applicability_gate": (
            "GO" if coverage_f >= TRIGGER_COVERAGE_GO else "NO-GO"
        ),
        "phase_4_reason": (
            f"published trigger_conditions coverage {coverage_f:.1%} "
            f"(threshold {TRIGGER_COVERAGE_GO:.0%})"
        ),
        "phase_8_flywheel": "GO" if feedback >= 50 else "NO-GO",
        "phase_8_reason": (
            f"{feedback} retrieval_feedback rows "
            "(threshold 50 for a minable golden set)"
        ),
        "n2_live": any(
            k in (audit.get("risk_tier_counts") or {})
            for k in ("minimal", "critical", "restricted")
        ),
        "n1_blast_radius": int(audit.get("approved_unpublished_current") or 0),
        "n3_stale_heuristic": int(
            audit.get("approved_embedding_older_than_latest_published") or 0
        ),
    }


async def main() -> int:
    async with async_session_factory() as db:
        await bind_session_tenant(db, None, bypass=True)
        row = (await db.execute(_SQL)).one()
        audit = dict(row[0])
    audit["gates"] = _gates(audit)
    json.dump(audit, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
