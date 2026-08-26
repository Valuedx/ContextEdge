"""Count (and optionally draft) playbook-selection eval cases from episodes.

Phase 0 found zero ``retrieval_feedback`` rows. The plan's other mining
path does not need feedback: resolved/approved episodes whose pattern
has an approved playbook (``Playbook.pattern_id``), plus the stronger
``validated_fix`` episode→playbook edges.

    python -m scripts.mine_playbook_selection_cases
    python -m scripts.mine_playbook_selection_cases --write \\
        src/contextedge/evals/datasets/playbook_selection_mined.jsonl

Read-only against Postgres. Sets ``app.bypass_rls``. Default is count-only.
Mined labels are weaker than human-confirmed feedback — stamp ``source``
and ``source_path`` so eval can segment them.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text

from contextedge.database import async_session_factory
from contextedge.tenant_rls import bind_session_tenant

FLOOR = 120
HOLDOUT_FRAC = 0.30

_COUNT_SQL = text(
    """
    WITH pel AS (
      SELECT
        e.id AS episode_id,
        e.tenant_id,
        e.reviewer_state,
        p.id AS playbook_id,
        p.stable_key
      FROM pattern_evidence_links pel
      JOIN episodes e
        ON e.id = pel.episode_id AND e.tenant_id = pel.tenant_id
      JOIN playbooks p
        ON p.pattern_id = pel.pattern_id AND p.tenant_id = pel.tenant_id
      WHERE pel.episode_id IS NOT NULL
        AND p.lifecycle_state = 'approved'
        AND p.pattern_id IS NOT NULL
    ),
    belongs AS (
      SELECT
        e.id AS episode_id,
        e.tenant_id,
        e.reviewer_state,
        p.id AS playbook_id,
        p.stable_key
      FROM graph_edges ge
      JOIN episodes e
        ON e.id = ge.source_node_id AND e.tenant_id = ge.tenant_id
      JOIN playbooks p
        ON p.pattern_id = ge.target_node_id AND p.tenant_id = ge.tenant_id
      WHERE ge.edge_type = 'belongs_to'
        AND ge.source_node_type = 'episode'
        AND ge.target_node_type = 'pattern'
        AND ge.valid_to IS NULL
        AND p.lifecycle_state = 'approved'
        AND p.pattern_id IS NOT NULL
    ),
    validated AS (
      SELECT
        e.id AS episode_id,
        e.tenant_id,
        e.reviewer_state,
        p.id AS playbook_id,
        p.stable_key
      FROM graph_edges ge
      JOIN episodes e
        ON e.id = ge.source_node_id AND e.tenant_id = ge.tenant_id
      JOIN playbooks p
        ON p.id = ge.target_node_id AND p.tenant_id = ge.tenant_id
      WHERE ge.edge_type = 'validated_fix'
        AND ge.source_node_type = 'episode'
        AND ge.target_node_type = 'playbook'
        AND ge.valid_to IS NULL
        AND p.lifecycle_state = 'approved'
    ),
    usable_belongs AS (
      SELECT episode_id, tenant_id, array_agg(DISTINCT stable_key) AS keys
      FROM belongs
      WHERE reviewer_state = 'approved'
      GROUP BY episode_id, tenant_id
    ),
    usable_pel AS (
      SELECT episode_id, tenant_id, array_agg(DISTINCT stable_key) AS keys
      FROM pel
      WHERE reviewer_state = 'approved'
      GROUP BY episode_id, tenant_id
    ),
    usable_validated AS (
      SELECT episode_id, tenant_id, array_agg(DISTINCT stable_key) AS keys
      FROM validated
      WHERE reviewer_state = 'approved'
      GROUP BY episode_id, tenant_id
    )
    SELECT jsonb_build_object(
      'retrieval_feedback_rows', (SELECT count(*) FROM retrieval_feedback),
      'playbooks_total', (SELECT count(*) FROM playbooks),
      'playbooks_approved', (
        SELECT count(*) FROM playbooks WHERE lifecycle_state = 'approved'
      ),
      'approved_playbooks_with_pattern_id', (
        SELECT count(*) FROM playbooks
        WHERE lifecycle_state = 'approved' AND pattern_id IS NOT NULL
      ),
      'episodes_total', (SELECT count(*) FROM episodes),
      'episodes_approved', (
        SELECT count(*) FROM episodes WHERE reviewer_state = 'approved'
      ),
      'pattern_evidence_link_rows', (SELECT count(*) FROM pel),
      'pattern_evidence_link_approved_episodes', (
        SELECT count(DISTINCT episode_id) FROM pel WHERE reviewer_state = 'approved'
      ),
      'pattern_evidence_link_unambiguous', (
        SELECT count(*) FROM usable_pel WHERE cardinality(keys) = 1
      ),
      'pattern_evidence_link_ambiguous', (
        SELECT count(*) FROM usable_pel WHERE cardinality(keys) > 1
      ),
      'belongs_to_rows', (SELECT count(*) FROM belongs),
      'belongs_to_approved_episodes', (
        SELECT count(DISTINCT episode_id) FROM belongs WHERE reviewer_state = 'approved'
      ),
      'belongs_to_unambiguous', (
        SELECT count(*) FROM usable_belongs WHERE cardinality(keys) = 1
      ),
      'belongs_to_ambiguous', (
        SELECT count(*) FROM usable_belongs WHERE cardinality(keys) > 1
      ),
      'belongs_to_distinct_expected_keys', (
        SELECT count(DISTINCT keys[1]) FROM usable_belongs WHERE cardinality(keys) = 1
      ),
      'validated_fix_rows', (SELECT count(*) FROM validated),
      'validated_fix_approved_episodes', (
        SELECT count(DISTINCT episode_id) FROM validated WHERE reviewer_state = 'approved'
      ),
      'validated_fix_unambiguous', (
        SELECT count(*) FROM usable_validated WHERE cardinality(keys) = 1
      ),
      'validated_fix_ambiguous', (
        SELECT count(*) FROM usable_validated WHERE cardinality(keys) > 1
      ),
      'union_unambiguous_approved_episodes', (
        SELECT count(DISTINCT episode_id) FROM (
          SELECT episode_id FROM usable_belongs WHERE cardinality(keys) = 1
          UNION
          SELECT episode_id FROM usable_pel WHERE cardinality(keys) = 1
          UNION
          SELECT episode_id FROM usable_validated WHERE cardinality(keys) = 1
        ) u
      ),
      'tenants_with_unambiguous', (
        SELECT count(DISTINCT tenant_id) FROM (
          SELECT tenant_id FROM usable_belongs WHERE cardinality(keys) = 1
          UNION
          SELECT tenant_id FROM usable_pel WHERE cardinality(keys) = 1
          UNION
          SELECT tenant_id FROM usable_validated WHERE cardinality(keys) = 1
        ) t
      ),
      'playbook_lifecycle', (
        SELECT coalesce(jsonb_object_agg(s, n), '{}'::jsonb)
        FROM (
          SELECT coalesce(lifecycle_state, '<null>') AS s, count(*) AS n
          FROM playbooks GROUP BY lifecycle_state
        ) x
      ),
      'playbooks_with_pattern_id', (
        SELECT count(*) FROM playbooks WHERE pattern_id IS NOT NULL
      ),
      'belongs_to_episode_pattern_edges', (
        SELECT count(*) FROM graph_edges
        WHERE edge_type = 'belongs_to'
          AND source_node_type = 'episode'
          AND target_node_type = 'pattern'
          AND valid_to IS NULL
      ),
      'candidate_join_unambiguous_approved_episodes', (
        SELECT count(*) FROM (
          SELECT e.id
          FROM episodes e
          JOIN graph_edges ge
            ON ge.tenant_id = e.tenant_id
           AND ge.edge_type = 'belongs_to'
           AND ge.source_node_type = 'episode'
           AND ge.target_node_type = 'pattern'
           AND ge.source_node_id = e.id
           AND ge.valid_to IS NULL
          JOIN playbooks p
            ON p.tenant_id = e.tenant_id AND p.pattern_id = ge.target_node_id
          WHERE e.reviewer_state = 'approved'
            AND length(btrim(e.title)) >= 8
          GROUP BY e.id
          HAVING count(DISTINCT p.stable_key) = 1
        ) c
      ),
      'candidate_join_ambiguous', (
        SELECT count(*) FROM (
          SELECT e.id
          FROM episodes e
          JOIN graph_edges ge
            ON ge.tenant_id = e.tenant_id
           AND ge.edge_type = 'belongs_to'
           AND ge.source_node_type = 'episode'
           AND ge.target_node_type = 'pattern'
           AND ge.source_node_id = e.id
           AND ge.valid_to IS NULL
          JOIN playbooks p
            ON p.tenant_id = e.tenant_id AND p.pattern_id = ge.target_node_id
          WHERE e.reviewer_state = 'approved'
          GROUP BY e.id
          HAVING count(DISTINCT p.stable_key) > 1
        ) c
      ),
      'candidate_join_distinct_keys', (
        SELECT count(DISTINCT p.stable_key)
        FROM episodes e
        JOIN graph_edges ge
          ON ge.tenant_id = e.tenant_id
         AND ge.edge_type = 'belongs_to'
         AND ge.source_node_type = 'episode'
         AND ge.target_node_type = 'pattern'
         AND ge.source_node_id = e.id
         AND ge.valid_to IS NULL
        JOIN playbooks p
          ON p.tenant_id = e.tenant_id AND p.pattern_id = ge.target_node_id
        WHERE e.reviewer_state = 'approved'
      )
    ) AS audit
    """
)

_DRAFT_SQL = text(
    """
    WITH ranked AS (
      SELECT
        e.id AS episode_id,
        e.tenant_id,
        e.domain_id,
        e.title,
        e.root_cause_summary,
        e.final_outcome,
        e.entity_refs,
        e.generation_provenance AS episode_generation_provenance,
        p.stable_key,
        p.id AS playbook_id,
        p.pattern_id,
        p.risk_tier,
        p.lifecycle_state,
        pv.generation_provenance AS playbook_generation_provenance,
        CASE
          WHEN ge_fix.id IS NOT NULL THEN 'validated_fix'
          WHEN pel.id IS NOT NULL THEN 'pattern_evidence_link'
          ELSE 'belongs_to_pattern'
        END AS source_path,
        CASE WHEN ge_fix.id IS NOT NULL THEN 0
             WHEN pel.id IS NOT NULL THEN 1
             ELSE 2 END AS path_rank
      FROM episodes e
      JOIN playbooks p
        ON p.tenant_id = e.tenant_id
       AND (
         p.lifecycle_state = 'approved'
         OR CAST(:include_candidate AS boolean)
       )
      LEFT JOIN playbook_versions pv ON pv.id = p.current_version_id
      LEFT JOIN graph_edges ge_pat
        ON ge_pat.tenant_id = e.tenant_id
       AND ge_pat.edge_type = 'belongs_to'
       AND ge_pat.source_node_type = 'episode'
       AND ge_pat.target_node_type = 'pattern'
       AND ge_pat.source_node_id = e.id
       AND ge_pat.target_node_id = p.pattern_id
       AND ge_pat.valid_to IS NULL
      LEFT JOIN pattern_evidence_links pel
        ON pel.tenant_id = e.tenant_id
       AND pel.episode_id = e.id
       AND pel.pattern_id = p.pattern_id
      LEFT JOIN graph_edges ge_fix
        ON ge_fix.tenant_id = e.tenant_id
       AND ge_fix.edge_type = 'validated_fix'
       AND ge_fix.source_node_type = 'episode'
       AND ge_fix.target_node_type = 'playbook'
       AND ge_fix.source_node_id = e.id
       AND ge_fix.target_node_id = p.id
       AND ge_fix.valid_to IS NULL
      WHERE e.reviewer_state = 'approved'
        AND length(btrim(e.title)) >= 8
        AND (
          ge_pat.id IS NOT NULL
          OR pel.id IS NOT NULL
          OR ge_fix.id IS NOT NULL
        )
    ),
    unambiguous AS (
      SELECT episode_id
      FROM ranked
      GROUP BY episode_id
      HAVING count(DISTINCT stable_key) = 1
    ),
    preferred AS (
      SELECT DISTINCT ON (r.episode_id)
        r.episode_id, r.tenant_id, r.domain_id, r.title, r.root_cause_summary,
        r.final_outcome, r.entity_refs, r.episode_generation_provenance,
        r.stable_key, r.playbook_id, r.pattern_id, r.risk_tier, r.lifecycle_state,
        r.playbook_generation_provenance, r.source_path
      FROM ranked r
      JOIN unambiguous u ON u.episode_id = r.episode_id
      ORDER BY r.episode_id, r.path_rank
    )
    SELECT * FROM preferred
    ORDER BY tenant_id, episode_id
    """
)


def _entity_names(entity_refs) -> list[str]:
    if not isinstance(entity_refs, dict):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in entity_refs.get("identities") or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("canonical_name") or item.get("name") or "").strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names[:12]


def _symptoms(title: str, root_cause: str | None) -> list[str]:
    parts = [title.strip()]
    if root_cause and root_cause.strip() and root_cause.strip() != title.strip():
        parts.append(root_cause.strip()[:500])
    return parts


def _split_for(episode_id) -> str:
    digest = hashlib.sha256(str(episode_id).encode("utf-8")).digest()
    bucket = digest[0] / 255.0
    return "holdout" if bucket < HOLDOUT_FRAC else "train"


def _provenance_kind(raw) -> str | None:
    if not isinstance(raw, dict):
        return None
    prompt = raw.get("prompt_name") or raw.get("task")
    return "generated" if prompt else "unknown"


def _row_to_case(row) -> dict:
    mapping = row._mapping
    episode_id = mapping["episode_id"]
    pb_kind = _provenance_kind(mapping.get("playbook_generation_provenance"))
    return {
        "id": f"mined-{episode_id}",
        "source": "mined",
        "source_path": mapping["source_path"],
        "review_status": "draft",
        "split": _split_for(episode_id),
        "symptoms": _symptoms(mapping["title"], mapping.get("root_cause_summary")),
        "entities": _entity_names(mapping.get("entity_refs")),
        "context": (mapping.get("final_outcome") or "")[:500] or None,
        "expected_playbook_stable_key": mapping["stable_key"],
        "domain_id": str(mapping["domain_id"]) if mapping.get("domain_id") else None,
        "max_risk_tier": mapping.get("risk_tier") or "high",
        "episode_id": str(episode_id),
        "playbook_id": str(mapping["playbook_id"]),
        "pattern_id": str(mapping["pattern_id"]) if mapping.get("pattern_id") else None,
        "tenant_id": str(mapping["tenant_id"]),
        "generation_provenance": pb_kind,
        "playbook_lifecycle_state": mapping.get("lifecycle_state"),
        "notes": (
            "Mined draft: pattern_id / validated_fix asserts the playbook "
            "addresses this episode's pattern, not that a human confirmed "
            "it for this ticket. SME-review before treating as a label."
        ),
    }


def _gates(audit: dict) -> dict:
    union_n = int(audit.get("union_unambiguous_approved_episodes") or 0)
    candidate_n = int(audit.get("candidate_join_unambiguous_approved_episodes") or 0)
    belongs_n = int(audit.get("belongs_to_unambiguous") or 0)
    validated_n = int(audit.get("validated_fix_unambiguous") or 0)
    feedback = int(audit.get("retrieval_feedback_rows") or 0)
    keys = int(audit.get("belongs_to_distinct_expected_keys") or 0)
    cand_keys = int(audit.get("candidate_join_distinct_keys") or 0)
    ranking_viable = union_n >= FLOOR
    draft_viable = candidate_n >= FLOOR
    reason = (
        f"{union_n} unambiguous drafts against approved playbooks "
        f"(floor {FLOOR}); belongs_to={belongs_n}, "
        f"validated_fix={validated_n}, retrieval_feedback={feedback}, "
        f"distinct approved keys={keys}"
    )
    if not ranking_viable and draft_viable:
        reason += (
            f"; {candidate_n} unambiguous drafts exist against "
            f"candidate playbooks ({cand_keys} distinct keys) — usable "
            "for SME review, not as a ranking baseline until those "
            "playbooks are approved"
        )
    return {
        "golden_mine_viable": ranking_viable,
        "sme_draft_mine_viable": draft_viable,
        "reason": reason,
        "caveat": (
            "Labels are weaker than retrieval_feedback. Mined cases share "
            "the graph R3/R4 retrieve from — segment eval on source. "
            "Current ranker drops non-approved playbooks, so candidate "
            "expected keys score zero until approval."
        ),
    }


async def _count() -> dict:
    async with async_session_factory() as db:
        await bind_session_tenant(db, None, bypass=True)
        row = (await db.execute(_COUNT_SQL)).one()
        audit = dict(row[0])
    audit["gates"] = _gates(audit)
    return audit


async def _drafts(*, include_candidate: bool) -> list[dict]:
    async with async_session_factory() as db:
        await bind_session_tenant(db, None, bypass=True)
        rows = (
            await db.execute(
                _DRAFT_SQL, {"include_candidate": include_candidate}
            )
        ).all()
    cases = [_row_to_case(row) for row in rows]
    # Collapse any leftover duplicate episode ids (should already be 1:1).
    by_id: dict[str, dict] = {}
    for case in cases:
        by_id.setdefault(case["id"], case)
    return list(by_id.values())


def _write_jsonl(path: Path, cases: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "// Playbook-selection golden drafts mined from approved episodes.\n"
        "// source=mined, review_status=draft. SME-review before scoring as a baseline.\n"
        "// Required: symptoms, entities, expected_playbook_stable_key\n"
        "// Provenance: source (mined|authored), source_path, generation_provenance, split\n"
    )
    with path.open("w", encoding="utf-8") as fh:
        fh.write(header)
        for case in cases:
            fh.write(json.dumps(case, default=str) + "\n")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        help="Write mined JSONL drafts to this path (count-only if omitted).",
    )
    parser.add_argument(
        "--include-candidate-playbooks",
        action="store_true",
        help=(
            "Include playbooks that are not yet approved. Needed today: "
            "all 440 playbooks are candidate. Do not score these as a "
            "ranking baseline until they are approved."
        ),
    )
    args = parser.parse_args(argv)
    audit = await _count()
    json.dump(audit, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    if args.write is None:
        return 0
    if not audit["gates"]["golden_mine_viable"] and not args.include_candidate_playbooks:
        print(
            "Refusing to write: no approved-playbook drafts. "
            "Pass --include-candidate-playbooks to emit SME-review drafts.",
            file=sys.stderr,
        )
        return 2
    cases = await _drafts(include_candidate=args.include_candidate_playbooks)
    _write_jsonl(args.write, cases)
    summary = {
        "wrote": str(args.write),
        "drafts": len(cases),
        "train": sum(1 for c in cases if c["split"] == "train"),
        "holdout": sum(1 for c in cases if c["split"] == "holdout"),
        "source_paths": {},
    }
    for case in cases:
        key = case["source_path"]
        summary["source_paths"][key] = summary["source_paths"].get(key, 0) + 1
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
