import uuid
from uuid import uuid4 as _uuid4


APP_ROOT_NAMESPACE: uuid.UUID = uuid.uuid5(uuid.NAMESPACE_DNS, "pipeui.v1")
"""app namespace for creating stable seed. per-table namespaces are derived from this."""


def new_id() -> uuid.UUID:
    """function for creating a new random UUID
    """
    return _uuid4()


def table_namespace(table_name: str) -> uuid.UUID:
    """uses the app namespace in order to generate a per-table namespace that derives from the app one.
    """
    return uuid.uuid5(APP_ROOT_NAMESPACE, table_name)


def content_hash_id(table_name: str, *fields: str) -> uuid.UUID:
    """a deterministic hash used for multiple tables in the app.
    Used for quick lookups and confirmations.
    """
    return uuid.uuid5(table_namespace(table_name), "|".join(fields))
