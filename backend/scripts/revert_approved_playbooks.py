"""Script to revert all approved playbooks back to candidate state.

Usage:
    # 1. Preview changes (Dry Run - default, writes nothing):
    python -m scripts.revert_approved_playbooks

    # 2. Apply changes:
    python -m scripts.revert_approved_playbooks --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select, text
from contextedge.database import async_session_factory
from contextedge.tenant_rls import bind_session_tenant
from contextedge.models.playbook import Playbook, PlaybookApproval


async def revert_playbooks(apply_changes: bool = False) -> int:
    async with async_session_factory() as db:
        await bind_session_tenant(db, None, bypass=True)

        # 1. Fetch all approved playbooks
        stmt = select(Playbook).where(Playbook.lifecycle_state == "approved")
        result = await db.execute(stmt)
        approved_playbooks = list(result.scalars().all())

        total = len(approved_playbooks)
        print(f"[*] Found {total} playbooks currently in 'approved' state.")

        if total == 0:
            print("[OK] No approved playbooks found. Nothing to revert.")
            return 0

        print("\nPlaybooks to be reverted:")
        for idx, pb in enumerate(approved_playbooks, 1):
            print(f"  {idx}. [{pb.id}] {pb.title} (tenant: {pb.tenant_id})")

        if not apply_changes:
            print("\n[!] DRY RUN ONLY. No changes were made to the database.")
            print("[!] Run with `--apply` to commit these changes.")
            return 0

        print(f"\n[*] Applying changes: reverting {total} playbooks to 'candidate'...")

        playbook_ids = [pb.id for pb in approved_playbooks]

        # Bypass user triggers and RLS during bulk maintenance
        await db.execute(text("SET LOCAL session_replication_role = 'replica';"))
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false);"))

        # 2. Unpublish associated versions (clear published_at and published_by)
        unpublish_stmt = text(
            """
            UPDATE playbook_versions
            SET published_at = NULL,
                published_by = NULL
            WHERE playbook_id = ANY(:pb_ids)
              AND published_at IS NOT NULL
            """
        )
        await db.execute(unpublish_stmt, {"pb_ids": playbook_ids})

        # 3. Update playbooks to 'candidate' and clear approval timestamps/approver
        revert_stmt = text(
            """
            UPDATE playbooks
            SET lifecycle_state = 'candidate',
                approver_user_id = NULL,
                last_validated_at = NULL,
                updated_at = NOW()
            WHERE id = ANY(:pb_ids)
            """
        )
        await db.execute(revert_stmt, {"pb_ids": playbook_ids})

        # 4. Insert audit / approval tracking records
        system_actor_id = uuid.UUID("00000000-0000-0000-0000-000000000010")
        for pb in approved_playbooks:
            approval = PlaybookApproval(
                tenant_id=pb.tenant_id,
                playbook_id=pb.id,
                playbook_version_id=pb.current_version_id,
                approver_id=pb.approver_user_id or system_actor_id,
                action="candidate",
                comments="Bulk reverted to candidate state: unreviewed playbook reset.",
            )
            db.add(approval)

        await db.commit()
        print(f"[OK] Successfully reverted {total} playbooks to 'candidate' state.")
        print("[OK] Cleared version publish timestamps and recorded audit approvals.")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Revert all approved playbooks back to candidate state."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply changes to the database (default is dry-run preview).",
    )
    args = parser.parse_args()
    return asyncio.run(revert_playbooks(apply_changes=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
