"""Move the knowledge-derived episodes into knowledge cases, and tombstone them.

299 episodes were reconstructed from KB articles alone. Each asserts that
something happened; each was really a document claiming a resolution
works. They were marked `reviewer_state='invalidated'` when found, which
kept them out of review, clustering and the agent — but a row that still
lives in `episodes` can be revived by any future code path that widens a
filter, and the whole point of the split is that it should not be possible.

So: migrate the content, then tombstone the row.

WHAT IS CARRIED OVER -- the reconstruction, which is the valuable part and
is often the only structured description of a failure mode nobody has hit:

    title, root_cause_summary -> documented_cause, entity_refs,
    embedding, extraction_confidence, and every step

Two fields are re-labelled rather than copied, and both are worth naming
because each looks like carrying an outcome across:

`final_outcome` becomes `documented_resolution`. For these rows the value
was never an observation -- the extractor wrote it out of the article's
resolution section and it was then mislabelled as something that happened.
Moving it restores what it always was. The original field name is recorded
in provenance so the substitution is auditable rather than silent.

`episode_steps.observation` becomes `expected_outcome` for the same
reason: on an episode it means "what was seen when this was done", and on
these rows nobody did anything -- it is the article's account of what the
step achieves, which is exactly what `expected_outcome` means on a
documented step.

WHAT IS NOT CARRIED OVER -- everything that only makes sense for something
that occurred: reviewer_state, status, ai_review, cluster_fingerprint,
contradictions between accounts (there is one account), and the step flags
failed_flag / successful_flag / result_state. A documented step describes
an action to take, not one that was taken.

MULTI-SOURCE EPISODES. 40 of the 299 were synthesised across several
articles. A knowledge case is one source document's claim, so these are
migrated against their first article with the full source list kept in
provenance under `synthesised_from_evidence_ids` and a `needs_review` flag:
a synthesis across documents is really a candidate pattern, and turning it
into one belongs to the pattern layer, not to this migration. Where two
episodes resolve to the same first article the insert is skipped by the
unique constraint rather than overwriting -- the count is reported.

TOMBSTONES. Rows are copied verbatim into
`episodes_knowledge_migrated_backup` and
`episode_steps_knowledge_migrated_backup` before deletion, the same way
0071 preserved 53,288 deduplicated steps. `episode_evidence_links` and
`episode_issue_signatures` cascade; `pattern_evidence_links` does not, and
does not need to -- these episodes have zero, verified before writing this.

Revision ID: 0073_migrate_knowledge_episodes_to_cases
Revises: 0072_knowledge_case_and_pattern_evidence
"""

import sqlalchemy as sa

from alembic import op

revision = "0073_migrate_knowledge_episodes_to_cases"
down_revision = "0072_knowledge_case_and_pattern_evidence"
branch_labels = None
depends_on = None

_SELECT_TARGETS = """
    SELECT id FROM episodes
    WHERE reviewer_state = 'invalidated'
      AND generation_provenance->>'invalid_reason' = 'source_not_observational'
"""


def upgrade() -> None:
    bind = op.get_bind()

    # Nothing to do on a database that never ran the affected pipeline.
    target_count = bind.execute(
        sa.text(f"SELECT count(*) FROM ({_SELECT_TARGETS}) t")
    ).scalar_one()
    if not target_count:
        return

    # --- tombstones, before anything is removed --------------------------
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS episodes_knowledge_migrated_backup
            AS SELECT * FROM episodes WHERE id IN ({_SELECT_TARGETS})
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS episode_steps_knowledge_migrated_backup
            AS SELECT * FROM episode_steps
            WHERE episode_id IN ({_SELECT_TARGETS})
            """
        )
    )

    # --- resolve each episode to its source document ---------------------
    # Restricted to knowledge types so a stray non-knowledge id can never
    # become the "source document" of a case.
    #
    # 296 of the 299 resolve to just 116 distinct articles: the same article
    # was reconstructed many times over, which is the duplicate-synthesis
    # problem the growth gate exists for, showing up again here. One article
    # is one case, so the duplicates have to collapse — and WHICH survivor
    # is kept matters. `ON CONFLICT DO NOTHING` alone would keep whichever
    # row the planner happened to insert first, so instead the richest
    # reconstruction wins: most steps, then highest extraction confidence,
    # then newest. Same principle as 0071 keeping the best step per order.
    bind.execute(
        sa.text(
            f"""
            CREATE TEMP TABLE _kc_migration AS
            SELECT e.id AS episode_id,
                   src.evidence_id,
                   row_number() OVER (
                       PARTITION BY e.tenant_id, src.evidence_id
                       ORDER BY (
                           SELECT count(*) FROM episode_steps s
                           WHERE s.episode_id = e.id
                       ) DESC,
                       e.extraction_confidence DESC,
                       e.created_at DESC
                   ) AS rank_for_source
            FROM episodes e
            JOIN LATERAL (
                SELECT (x.eid)::uuid AS evidence_id
                FROM jsonb_array_elements_text(coalesce(e.evidence_ids,'[]'::jsonb)) AS x(eid)
                JOIN evidence_items k ON k.id = (x.eid)::uuid
                WHERE k.evidence_type IN ('kb_article', 'sop', 'runbook', 'documentation')
                ORDER BY k.created_at_source NULLS LAST, k.id
                LIMIT 1
            ) src ON true
            WHERE e.id IN ({_SELECT_TARGETS})
            """
        )
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO knowledge_cases (
                id, tenant_id, workspace_id, domain_id,
                source_evidence_id, source_kind, source_authority,
                title, documented_cause, documented_resolution,
                entity_refs, applicability, extraction_confidence,
                embedding, migrated_from_episode_id, generation_provenance,
                created_at, updated_at
            )
            SELECT
                gen_random_uuid(), e.tenant_id, e.workspace_id, e.domain_id,
                src.evidence_id,
                coalesce(ev.evidence_type, 'kb_article'),
                'internal_kb',
                e.title,
                e.root_cause_summary,
                e.final_outcome,
                e.entity_refs,
                ev.applicability,
                e.extraction_confidence,
                e.embedding,
                e.id,
                coalesce(e.generation_provenance, '{}'::jsonb) || jsonb_build_object(
                    'migrated_by', '0073_migrate_knowledge_episodes_to_cases',
                    'migration_reason', 'knowledge evidence reconstructed as episode',
                    'documented_resolution_from', 'episodes.final_outcome',
                    'synthesised_from_evidence_ids', e.evidence_ids,
                    'needs_review', jsonb_array_length(coalesce(e.evidence_ids,'[]'::jsonb)) > 1
                ),
                e.created_at, now()
            FROM _kc_migration src
            JOIN episodes e ON e.id = src.episode_id
            LEFT JOIN evidence_items ev ON ev.id = src.evidence_id
            WHERE src.rank_for_source = 1
            ON CONFLICT (tenant_id, source_evidence_id) DO NOTHING
            """
        )
    )

    # --- the steps ------------------------------------------------------
    # Only the fields a documented action has. The outcome flags are left
    # behind deliberately: see the module docstring.
    bind.execute(
        sa.text(
            """
            INSERT INTO knowledge_case_steps (
                id, knowledge_case_id, step_order, step_type, text,
                expected_outcome, extraction_confidence, evidence_refs, created_at
            )
            SELECT
                gen_random_uuid(), kc.id, s.step_order,
                coalesce(s.step_type, 'action'), s.text,
                s.observation, s.extraction_confidence, s.evidence_refs, now()
            FROM knowledge_cases kc
            JOIN episode_steps s ON s.episode_id = kc.migrated_from_episode_id
            WHERE kc.migrated_from_episode_id IS NOT NULL
            ON CONFLICT (knowledge_case_id, step_order) DO NOTHING
            """
        )
    )

    # --- remove the originals -------------------------------------------
    # Every episode whose SOURCE ARTICLE is now represented by a case, not
    # only the winner that became one. The ~180 runners-up are redundant
    # reconstructions of an article that already has its case, so leaving
    # them in `episodes` would keep exactly the rows this whole change
    # exists to get rid of. Their content is in the tombstone either way,
    # and the winner carries the richest version of it.
    #
    # What is NOT deleted: an episode that resolved to no knowledge source
    # at all. Nothing represents it, so it stays `invalidated` -- out of
    # review, clustering and the agent -- rather than disappearing from
    # both live tables. Migrate-then-delete must never become
    # delete-without-migrate.
    #
    # episode_steps has no cascade, so it goes first; evidence links and
    # issue signatures cascade with the episode row.
    represented = """
        SELECT m.episode_id FROM _kc_migration m
        JOIN knowledge_cases kc
          ON kc.source_evidence_id = m.evidence_id
    """
    bind.execute(
        sa.text(f"DELETE FROM episode_steps WHERE episode_id IN ({represented})")
    )
    deleted = bind.execute(
        sa.text(f"DELETE FROM episodes WHERE id IN ({represented})")
    ).rowcount

    cases = bind.execute(
        sa.text(
            "SELECT count(*) FROM knowledge_cases "
            "WHERE migrated_from_episode_id IS NOT NULL"
        )
    ).scalar_one()
    # An episode left behind is not an error, but it is a fact somebody
    # needs, reported rather than discovered later.
    remaining = bind.execute(
        sa.text(f"SELECT count(*) FROM ({_SELECT_TARGETS}) t")
    ).scalar_one()
    print(
        f"0073: {cases} knowledge cases created from {deleted} episodes "
        f"(duplicate reconstructions of the same article collapsed into one "
        f"case each); {remaining} episodes left invalidated with no knowledge "
        f"source to migrate to"
    )


def downgrade() -> None:
    """Put the episodes back from the tombstones and drop the cases.

    Best-effort by design: it restores the rows this migration removed, and
    it cannot un-know that they were the wrong shape.
    """
    bind = op.get_bind()
    for table in (
        "episodes_knowledge_migrated_backup",
        "episode_steps_knowledge_migrated_backup",
    ):
        exists = bind.execute(
            sa.text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}
        ).scalar()
        if not exists:
            return

    bind.execute(
        sa.text(
            "INSERT INTO episodes SELECT * FROM episodes_knowledge_migrated_backup "
            "ON CONFLICT (id) DO NOTHING"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO episode_steps SELECT * FROM "
            "episode_steps_knowledge_migrated_backup ON CONFLICT (id) DO NOTHING"
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM knowledge_case_steps WHERE knowledge_case_id IN "
            "(SELECT id FROM knowledge_cases WHERE migrated_from_episode_id IS NOT NULL)"
        )
    )
    bind.execute(
        sa.text("DELETE FROM knowledge_cases WHERE migrated_from_episode_id IS NOT NULL")
    )
    op.execute("DROP TABLE IF EXISTS episode_steps_knowledge_migrated_backup")
    op.execute("DROP TABLE IF EXISTS episodes_knowledge_migrated_backup")
