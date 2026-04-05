"""Initial schema: pgvector + all application tables.

Uses metadata.create_all on upgrade so the first revision matches SQLAlchemy models
without requiring a live DB at commit time for autogenerate. Prefer follow-up
revisions with explicit op.create_table for production change control.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_models_loaded() -> None:
    import contextedge.models.audit  # noqa: F401
    import contextedge.models.episode  # noqa: F401
    import contextedge.models.evaluation  # noqa: F401
    import contextedge.models.evidence  # noqa: F401
    import contextedge.models.pattern  # noqa: F401
    import contextedge.models.playbook  # noqa: F401
    import contextedge.models.source  # noqa: F401
    import contextedge.models.tenant  # noqa: F401


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    bind = op.get_bind()
    _ensure_models_loaded()
    from contextedge.models.base import Base

    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    _ensure_models_loaded()
    from contextedge.models.base import Base

    Base.metadata.drop_all(bind=bind)
    op.execute(sa.text("DROP EXTENSION IF EXISTS vector CASCADE"))
