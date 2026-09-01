"""Seed tenant quality policy pack and ontology from JSON.

Generic tenants get empty templates. Product-specific vocabulary lives under
``backend/data/quality/examples/<profile>/`` and is loaded only when you pass
``--profile`` or explicit JSON paths — never from Python code.

    python backend/scripts/seed_quality_policy_pack.py
    python backend/scripts/seed_quality_policy_pack.py --tenant <uuid>
    python backend/scripts/seed_quality_policy_pack.py --tenant <uuid> --profile automationedge
    python backend/scripts/seed_quality_policy_pack.py --tenant <uuid> \\
        --policy-pack path/to/policy_pack.json --ontology path/to/ontology.json

    python backend/scripts/seed_quality_policy_pack.py --list-profiles
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from contextedge.config import settings  # noqa: E402
from contextedge.models.tenant import Tenant  # noqa: E402
from contextedge.quality.seed_data import list_quality_profiles  # noqa: E402
from contextedge.services.quality_policy_service import (  # noqa: E402
    seed_ontology,
    seed_policy_pack,
)


async def seed_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    profile: str | None,
    policy_pack: Path | None,
    ontology: Path | None,
) -> None:
    pack = await seed_policy_pack(
        db,
        tenant_id,
        profile=profile,
        payload_path=policy_pack,
    )
    ont = await seed_ontology(
        db,
        tenant_id,
        profile=profile,
        payload_path=ontology,
    )
    label = profile or "template"
    print(f"  tenant {tenant_id} [{label}]: pack v{pack.version}, ontology v{ont.version}")


async def run(
    dsn: str,
    tenant_id: uuid.UUID | None,
    *,
    profile: str | None,
    policy_pack: Path | None,
    ontology: Path | None,
) -> int:
    engine = create_async_engine(dsn)
    async with AsyncSession(engine) as db:
        if tenant_id is not None:
            await seed_tenant(
                db,
                tenant_id,
                profile=profile,
                policy_pack=policy_pack,
                ontology=ontology,
            )
        else:
            rows = (await db.execute(sa.select(Tenant.id))).scalars().all()
            if not rows:
                print("No tenants found; pass --tenant or create one first.")
                return 1
            for tid in rows:
                await seed_tenant(
                    db,
                    tid,
                    profile=profile,
                    policy_pack=policy_pack,
                    ontology=ontology,
                )
        await db.commit()

    await engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=settings.database_url, help="Async SQLAlchemy DSN")
    parser.add_argument("--tenant", type=uuid.UUID, default=None, help="Single tenant UUID")
    parser.add_argument(
        "--profile",
        default=None,
        help="Load examples/<profile>/ JSON (product-specific, optional)",
    )
    parser.add_argument(
        "--policy-pack",
        type=Path,
        default=None,
        help="Custom policy_pack.json path (overrides --profile for pack only)",
    )
    parser.add_argument(
        "--ontology",
        type=Path,
        default=None,
        help="Custom ontology.json path (overrides --profile for ontology only)",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="List available example profiles and exit",
    )
    args = parser.parse_args()

    if args.list_profiles:
        profiles = list_quality_profiles()
        if not profiles:
            print("No example profiles under backend/data/quality/examples/")
        else:
            for name in profiles:
                print(name)
        return 0

    if args.profile and args.profile not in list_quality_profiles():
        print(
            f"Unknown profile {args.profile!r}. "
            f"Available: {', '.join(list_quality_profiles()) or '(none)'}"
        )
        return 1

    return asyncio.run(
        run(
            args.dsn,
            args.tenant,
            profile=args.profile,
            policy_pack=args.policy_pack,
            ontology=args.ontology,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
