"""Entity class taxonomy (backlog B1).

The generalization unit for fix transfer (Doc-3): LPT001 is an
instance of Dell Latitude 5420, which is a laptop, which is an
endpoint, which is a computing device. Class membership is one
dimension of applicability — traits (model, OS, components) are the
others and land with B2.

Design decisions:
- **Single-parent tree.** Doc-3's examples include ``windows_endpoint``,
  but OS is a cross-cutting dimension (a laptop is both portable and
  Windows) — modeling it as a class would force multi-parent edges.
  OS becomes a normalized TRAIT (B2 ``os_name``/``os_version``);
  "managed Windows endpoints" = class ``endpoint`` + trait filter.
- **Global taxonomy, deterministic ids.** Classes are not tenant data;
  rows use uuid5("contextedge:entity_class:<key>") so re-running the
  seed (or seeding from two environments) converges on identical ids.
  ``ON CONFLICT DO NOTHING`` keeps the seed idempotent.
- **``configuration_item`` is the root fallback** — unknown ServiceNow
  classes degrade to it, exactly mirroring today's entity_type
  fallback.

Additive and re-runnable.

Revision ID: 0042_entity_classes
Revises: 0041_message_function
Create Date: 2026-08-02 22:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0042_entity_classes"
down_revision: Union[str, None] = "0041_message_function"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (canonical_key, display_name, parent_key, class_family), topologically
# ordered so parents insert first. uuid5-deterministic ids below.
_SEED = [
    ("configuration_item", "Configuration Item", None, "general"),
    ("computing_device", "Computing Device", "configuration_item", "computing"),
    ("endpoint", "Endpoint", "computing_device", "computing"),
    ("portable_endpoint", "Portable Endpoint", "endpoint", "computing"),
    ("laptop", "Laptop", "portable_endpoint", "computing"),
    ("fixed_endpoint", "Fixed Endpoint", "endpoint", "computing"),
    ("desktop", "Desktop", "fixed_endpoint", "computing"),
    ("server", "Server", "computing_device", "computing"),
    ("database_server", "Database Server", "server", "computing"),
    ("network_device", "Network Device", "computing_device", "network"),
    ("application", "Application", "configuration_item", "software"),
    ("database", "Database", "configuration_item", "software"),
    ("business_service", "Business Service", "configuration_item", "service"),
]

_IDS = {
    "configuration_item": "d64807ed-385f-5c11-bb2f-cf96dd316c97",
    "computing_device": "83a014c4-6c79-5b29-8ae5-3c5d317710e6",
    "endpoint": "653c02ad-a663-59ef-a101-0f1a4a9cd2da",
    "portable_endpoint": "596b8d5a-f02f-534b-8980-1971fe022960",
    "laptop": "d746322a-91c8-53e3-b839-405c83e7009a",
    "fixed_endpoint": "c93d3ed7-163c-5162-ba51-f462b0abacc9",
    "desktop": "0b4ccf47-f8df-5c64-a28b-81949195c3ed",
    "server": "e0efaabc-fe2b-551f-82b0-459389fa7a7f",
    "database_server": "dd5020b3-75de-52b6-aae4-d91ec99e0c86",
    "network_device": "f12a6ac5-5fd6-5aa9-aaf4-335e9794da36",
    "application": "ee7ca9b8-7e85-5b20-9de2-47f4052640ce",
    "database": "123757a1-a0b0-57e4-9b19-9ce7c0a6c0a2",
    "business_service": "e662a041-690f-576d-bc86-46ddc5f865d5",
}


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_classes (
            id UUID PRIMARY KEY,
            canonical_key VARCHAR(80) NOT NULL,
            display_name VARCHAR(120) NOT NULL,
            parent_class_id UUID REFERENCES entity_classes(id),
            class_family VARCHAR(50) NOT NULL DEFAULT 'general',
            attributes_schema JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_entity_classes_key UNIQUE (canonical_key)
        );
        """
    )
    for key, display, parent, family in _SEED:
        parent_sql = f"'{_IDS[parent]}'" if parent else "NULL"
        op.execute(
            f"""
            INSERT INTO entity_classes
                (id, canonical_key, display_name, parent_class_id, class_family)
            VALUES
                ('{_IDS[key]}', '{key}', '{display}', {parent_sql}, '{family}')
            ON CONFLICT (canonical_key) DO NOTHING;
            """
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS entity_classes;")
