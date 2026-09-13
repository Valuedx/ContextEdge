"""Every mapped table is tenant-owned, except documented global catalogs."""

from __future__ import annotations

import pkgutil

import contextedge.models as models_pkg
from contextedge.models.base import Base

for _mod in pkgutil.iter_modules(models_pkg.__path__):
    __import__(f"contextedge.models.{_mod.name}")

# "msps" joins "tenants" here: it is the level ABOVE a tenant, so a
# tenant_id on it would be backwards. Everything below a tenant still
# needs one, which is what this test protects.
GLOBAL_TABLES = frozenset({"msps", "tenants", "role_nav_access", "entity_classes"})


def test_every_domain_table_has_non_null_tenant_id_fk():
    missing: list[str] = []
    nullable: list[str] = []
    no_fk: list[str] = []
    no_cascade: list[str] = []
    for table in Base.metadata.tables.values():
        if table.name in GLOBAL_TABLES:
            assert "tenant_id" not in table.c, table.name
            continue
        col = table.c.get("tenant_id")
        if col is None:
            missing.append(table.name)
            continue
        if col.nullable:
            nullable.append(table.name)
        fks = [
            fk
            for fk in table.foreign_keys
            if fk.parent.name == "tenant_id" and fk.column.table.name == "tenants"
        ]
        if not fks:
            no_fk.append(table.name)
        elif any((fk.ondelete or "").upper() != "CASCADE" for fk in fks):
            no_cascade.append(table.name)
    assert missing == [], f"tables without tenant_id: {missing}"
    assert nullable == [], f"nullable tenant_id: {nullable}"
    assert no_fk == [], f"tenant_id without FK to tenants: {no_fk}"
    assert no_cascade == [], f"tenant_id FK missing ON DELETE CASCADE: {no_cascade}"
