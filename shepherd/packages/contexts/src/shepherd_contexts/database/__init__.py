"""Database context for read-only SQL database access.

This module provides DatabaseContext, a read-only database context
with query validation and table access restrictions.

Example:
    from shepherd_contexts.database import DatabaseContext

    db = DatabaseContext(
        connection_string="postgresql://user:pass@localhost/mydb",
        database_name="mydb",
        allowed_tables=frozenset({"users", "orders"}),
        max_rows=1000,
    )
"""

from shepherd_contexts.database.context import DatabaseContext
from shepherd_contexts.database.effects import QueryExecuted

__all__ = [
    "DatabaseContext",
    "QueryExecuted",
]
