"""add player and goalkeeper vector tables

Revision ID: e2072121286f
Revises: 1c18f93b7afd
Create Date: 2026-07-29 00:00:00.000000

Two tables rather than one because pgvector columns have fixed
dimensionality and outfield/GK attribute spaces aren't comparable:
outfield style is described by 18 z-scored attributes, GK style by 6.
See DECISIONS.md for the attribute selection reasoning.

HNSW (not IVFFlat) chosen for the index: pgvector's docs recommend it
for read-heavy workloads with no separate build/train step, which fits
this static-snapshot dataset. Cosine distance ops since the API queries
with `<=>`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = 'e2072121286f'
down_revision: Union[str, Sequence[str], None] = '1c18f93b7afd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "player_vectors",
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.player_id"), primary_key=True),
        sa.Column("embedding", Vector(18), nullable=False),
    )
    op.execute(
        "CREATE INDEX ix_player_vectors_embedding_hnsw ON player_vectors "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "goalkeeper_vectors",
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.player_id"), primary_key=True),
        sa.Column("embedding", Vector(6), nullable=False),
    )
    op.execute(
        "CREATE INDEX ix_goalkeeper_vectors_embedding_hnsw ON goalkeeper_vectors "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("goalkeeper_vectors")
    op.drop_table("player_vectors")
