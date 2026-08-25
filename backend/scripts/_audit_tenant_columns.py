"""Print tenant_id coverage for every mapped table. Prefer pytest test_tenant_table_coverage."""
from __future__ import annotations

import pkgutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import contextedge.models as models_pkg
from contextedge.models.base import Base

for mod in pkgutil.iter_modules(models_pkg.__path__):
    __import__(f"contextedge.models.{mod.name}")

GLOBAL = frozenset({"tenants", "role_nav_access", "entity_classes"})


def main() -> None:
    missing: list[str] = []
    nullable: list[str] = []
    no_fk: list[str] = []
    ok = 0
    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
        name = table.name
        if name in GLOBAL:
            print(f"GLOBAL  {name}")
            continue
        col = table.c.get("tenant_id")
        if col is None:
            missing.append(name)
            print(f"MISSING {name}")
            continue
        fks = [
            fk
            for fk in table.foreign_keys
            if fk.parent.name == "tenant_id" and fk.column.table.name == "tenants"
        ]
        bits = []
        if col.nullable:
            nullable.append(name)
            bits.append("NULLABLE")
        if not fks:
            no_fk.append(name)
            bits.append("NO_FK")
        if bits:
            print(f"GAP     {name}  {','.join(bits)}")
        else:
            ok += 1
            print(f"OK      {name}")
    print("---")
    print(f"ok={ok} missing={missing} nullable={nullable} no_fk={no_fk}")
    if missing or nullable or no_fk:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
