"""ce_fill_tenant_id must not evaluate NEW.source_id on non-source tables."""

from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0091_fix_ce_fill_tenant_id_playbooks.py"
)


def test_fill_tenant_trigger_does_not_and_source_id_in_one_expression():
    source = _MIGRATION.read_text(encoding="utf-8")
    function = source.split("_FUNCTION = ", 1)[1].split("def upgrade", 1)[0]
    assert "AND NEW.source_id" not in function
    assert "IF TG_TABLE_NAME = 'source_credentials' THEN" in function
    assert "DROP TRIGGER IF EXISTS trg_ce_fill_tenant_id ON %I" in source
