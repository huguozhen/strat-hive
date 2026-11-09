from alembic import context

from strathive.adapters.schema import metadata

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=metadata, transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
