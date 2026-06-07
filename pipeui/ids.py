import uuid
from uuid import uuid4 as _uuid4


# §2: one stable seed; per-table namespaces are derived from this, not hardcoded
APP_ROOT_NAMESPACE: uuid.UUID = uuid.uuid5(uuid.NAMESPACE_DNS, "pipeui.v1")


def new_id() -> uuid.UUID:
    return _uuid4()


def table_namespace(table_name: str) -> uuid.UUID:
    return uuid.uuid5(APP_ROOT_NAMESPACE, table_name)


def content_hash_id(table_name: str, *fields: str) -> uuid.UUID:
    return uuid.uuid5(table_namespace(table_name), "|".join(fields))
