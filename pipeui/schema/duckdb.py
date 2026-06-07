import duckdb

from pipeui.schema.queries import DDL as _DDL


############################
# DuckDB Related Functions
############################
# Leaving it here in case in the future, we need to get away from DuckDB
def get_connection(db_path: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Establishes and returns a connection to a DuckDB database.

    This function creates a connection to a DuckDB database using the provided
    database file path. If no path is provided, it defaults to an in-memory
    database.

    :param db_path: The file path to the DuckDB database. Defaults to ":memory:"
                    which creates an in-memory database.
    :type db_path: str
    :return: A DuckDBPyConnection object representing the connection to the database.
    :rtype: duckdb.DuckDBPyConnection
    """
    return duckdb.connect(db_path)


def create_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Creates the necessary schema in the provided DuckDB connection.

    This function executes a predefined SQL Data Definition Language (DDL) statement
    to create database schema elements such as tables or other objects within the
    given DuckDB connection.

    :param conn: The DuckDB connection object to execute the schema creation
        DDL statement on.
    :type conn: duckdb.DuckDBPyConnection

    :return: None
    """
    conn.execute(_DDL)
