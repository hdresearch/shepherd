"""DatabaseContext: Read-only database with query validation.

This reference implementation demonstrates:
- NONE reversibility (queries can leak sensitive data)
- Query validation (SELECT only)
- Custom tool definition
- Table access restrictions

v2 API:
- extract_effects(sandbox, result): Extract query effects from result (PURE)
- apply_effect(effect): Returns self (database is read-only, no local state changes)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Self

from shepherd_core.types import (
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ReversibilityLevel,
    ToolCall,
    ToolDefinition,
    ValidationResult,
)
from shepherd_runtime.context import BindableContext

from shepherd_contexts.database.effects import QueryExecuted

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shepherd_core.effects import Effect
    from shepherd_runtime.context import Sandbox


@dataclass(frozen=True)
class DatabaseContext(BindableContext):
    """Read-only database context with query validation.

    Demonstrates:
    - NONE reversibility (information disclosure is irreversible)
    - SQL injection prevention via SELECT-only validation
    - Table access restrictions
    - Custom SQLQuery tool

    Lifecycle:
        configure(): Return binding with SQLQuery tool
        prepare(): Open connection (in real implementation)
        extract_effects(): Extract QueryExecuted effects from tool calls
        apply_effect(): Returns self (database is read-only)
        cleanup(): Close connection (in real implementation)
    """

    __binding_name__: ClassVar[str] = "database"

    connection_string: str
    database_name: str
    allowed_tables: frozenset[str] | None = None  # None = all tables
    max_rows: int = 1000

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.max_rows < 0:
            raise ValueError(f"max_rows must be non-negative, got {self.max_rows}")

    @property
    def context_id(self) -> str:
        return f"database:{self.database_name}"

    @property
    def reversibility(self) -> ReversibilityLevel:
        """Queries can leak sensitive info - irreversible."""
        return ReversibilityLevel.NONE

    def __str__(self) -> str:
        """Visible in prompts."""
        return self._build_description()

    # === Configuration ===

    def configure(
        self,
        capabilities: ProviderCapabilities | None = None,
    ) -> ProviderBinding:
        """Return binding with SQLQuery tool."""
        description = self._build_description()

        return ProviderBinding(
            context_id=self.context_id,
            context_type="DatabaseContext",
            context_description=description,
            custom_tools=(self._build_query_tool(),),
            validate_tool=self._validate_tool,
        )

    def _build_description(self) -> str:
        """Build context description."""
        lines = [
            f"Database: {self.database_name} (read-only)",
            f"Max rows per query: {self.max_rows}",
        ]
        if self.allowed_tables:
            lines.append(f"Allowed tables: {', '.join(sorted(self.allowed_tables))}")
        return "\n".join(lines)

    def _build_query_tool(self) -> ToolDefinition:
        """Build SQL query tool."""
        return ToolDefinition(
            name="SQLQuery",
            description=f"Execute read-only SQL query (max {self.max_rows} rows)",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL SELECT query to execute",
                    },
                },
                "required": ["query"],
            },
            handler=self._handle_query,
        )

    def _handle_query(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle SQL query (mock).

        In a real implementation, this would execute the query and return results.
        Effects are extracted from tool calls in extract_effects(), not tracked here.
        """
        # In real implementation: execute query against connection
        return {
            "columns": ["id", "name", "value"],
            "rows": [[1, "example", 100], [2, "data", 200]],
            "row_count": 2,
        }

    def _validate_tool(self, tool: ToolCall) -> ValidationResult:
        """Validate SQL queries."""
        if tool.name != "SQLQuery":
            return ValidationResult.allow(tool)

        query = tool.params.get("query", "").strip().upper()

        # Only allow SELECT
        if not query.startswith("SELECT"):
            return ValidationResult.reject(tool, "Only SELECT queries allowed (read-only)")

        # Block dangerous patterns
        dangerous = ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "TRUNCATE", "EXEC"]
        for d in dangerous:
            if d in query:
                return ValidationResult.reject(tool, f"Query contains forbidden keyword: {d}")

        # Check table restrictions (simplified)
        if self.allowed_tables:
            query_lower = tool.params.get("query", "").lower()
            found_allowed = False
            for table in self.allowed_tables:
                if table.lower() in query_lower:
                    found_allowed = True
                    break
            if not found_allowed:
                return ValidationResult.reject(
                    tool,
                    f"Query must reference allowed tables: {', '.join(self.allowed_tables)}",
                )

        return ValidationResult.allow(tool)

    # === v2 API ===

    def extract_effects(
        self,
        sandbox: Sandbox | None,
        result: ExecutionResult,
    ) -> Sequence[Effect]:
        """Extract query effects from tool calls. PURE.

        Database is read-only, so effects are for audit trail only.
        No state changes to derive.

        Args:
            sandbox: Ignored (database doesn't use filesystem sandbox)
            result: ExecutionResult containing tool calls

        Returns:
            Sequence of QueryExecuted effects
        """
        effects: list[Effect] = []

        for call, res in zip(result.tool_calls, result.tool_results, strict=False):
            if call.name == "SQLQuery" and res.success:
                row_count = 0
                if isinstance(res.output, dict):
                    row_count = res.output.get("row_count", 0)
                effects.append(
                    QueryExecuted(
                        database=self.database_name,
                        query_type="SELECT",
                        row_count=row_count,
                        context_id=self.context_id,
                    )
                )

        return effects

    def apply_effect(self, effect: Effect) -> Self:
        """Apply effect to derive new state. PURE.

        Database is read-only, so there's no local state to derive.
        Effects are for audit trail only.

        Args:
            effect: Effect to apply (ignored)

        Returns:
            Self (unchanged)
        """
        return self


__all__ = ["DatabaseContext"]
