"""Enable reviewed cancellation with a named state constraint."""
from alembic import op
import sqlalchemy as sa
revision='74b64d4324c1'
down_revision='09f515516f7a'
branch_labels=None
depends_on=None


def replace(states):
    constraints=sa.inspect(op.get_bind()).get_check_constraints('print_job')
    state_constraint=next(c for c in constraints if 'state' in c['sqltext'])
    convention={'ck':'ck_%(table_name)s_%(kind)s','kind':lambda c,t:str(c.sqltext).split()[0]}
    name=state_constraint['name'] or 'ck_print_job_state'
    with op.batch_alter_table('print_job',naming_convention=convention) as batch:
        batch.drop_constraint(name,type_='check')
        batch.create_check_constraint('ck_print_job_state',"state IN ("+','.join(repr(s) for s in states)+")")


def upgrade():
    replace(['queued','claimed','printing','completed','needs_review','cancelled'])


def downgrade():
    # Preserve cancelled jobs rather than inventing a completed print.
    op.execute("UPDATE print_job SET state='needs_review' WHERE state='cancelled'")
    replace(['queued','claimed','printing','completed','needs_review'])
