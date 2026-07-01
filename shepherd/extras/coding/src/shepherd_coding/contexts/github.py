"""GitHubContext: GitHub operations with PR management.

This context demonstrates:
- Wrapping GitHub utilities into a context
- Custom tool definitions (GetPRDetails, SubmitReview, PostComment)
- Effect capture for PR operations
- Token resolution chain
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Self

from shepherd_core import (
    Effect,
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ReversibilityLevel,
    ToolCall,
    ToolDefinition,
    ValidationResult,
)
from shepherd_runtime.context import BindableContext

from ..utils import get_pr_details, get_repo_from_git
from .effects import PRCommented, PRReviewSubmitted

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from shepherd_runtime.context import Sandbox


@dataclass
class GitHubContext(BindableContext):
    """GitHub operations context for PR management.

    Provides domain-specific context with:
    - Token resolution (explicit, env var, gh CLI)
    - Repository inference from git remote
    - Custom GitHub tools (GetPRDetails, SubmitReview, PostComment)
    - Effect capture for audit trails

    Lifecycle:
        configure(): Return binding with custom tools
        prepare(): Validate token availability
        extract_effects(): Parse tool calls to emit PRReviewSubmitted/PRCommented
        apply_effect(): No-op (audit effects don't change context config)
        cleanup(): No-op

    Example:
        # Basic usage with explicit repo
        github = GitHubContext(repo="owner/repo")

        # Infer repo from current directory
        github = GitHubContext()

        # With explicit token
        github = GitHubContext(
            repo="owner/repo",
            token="github_pat_...",
        )

        # Bind to scope by type
        scope.bind(github)
    """

    __binding_name__: ClassVar[str] = "github"

    repo: str | None = None
    token: str | None = None
    working_dir: Path | str | None = None
    allow_write_operations: bool = False  # Reviews, comments, labels

    def __post_init__(self) -> None:
        """Resolve repo if not explicitly provided."""
        if self.repo is None:
            with contextlib.suppress(ValueError):
                self.repo = get_repo_from_git(self.working_dir)

    @property
    def context_id(self) -> str:
        repo_part = self.repo or "unknown"
        mode = "write" if self.allow_write_operations else "readonly"
        return f"github:{repo_part}:{mode}"

    @property
    def reversibility(self) -> ReversibilityLevel:
        """Write operations are COMPENSABLE; read-only is AUTO."""
        if self.allow_write_operations:
            return ReversibilityLevel.COMPENSABLE
        return ReversibilityLevel.AUTO

    def __str__(self) -> str:
        """Visible - GitHub context should be in prompts."""
        return self._build_description()

    # === Configuration ===

    def configure(
        self,
        capabilities: ProviderCapabilities | None = None,
    ) -> ProviderBinding:
        """Return binding with custom GitHub tools.

        Uses abstract trust_level and require_confirmation - providers translate:
        - Claude: trust_level -> permission_mode, auto-generates MCP server name
        - OpenAI: trust_level -> guardrails configuration
        """
        description = self._build_description()
        custom_tools = self._build_tools()

        # Translate write capability to abstract trust level
        trust = "elevated" if self.allow_write_operations else "standard"

        # Require confirmation for write operations
        confirmations = frozenset({"SubmitPRReview", "PostPRComment"}) if self.allow_write_operations else frozenset()

        return ProviderBinding(
            context_id=self.context_id,
            context_type="GitHubContext",
            context_description=description,
            custom_tools=tuple(custom_tools),
            validate_tool=self._make_validator(),
            trust_level=trust,
            require_confirmation=confirmations,
        )

    def _build_description(self) -> str:
        """Build context description."""
        lines = [
            f"GitHub repository: {self.repo or '(to be inferred from git)'}",
        ]
        if self.allow_write_operations:
            lines.append("Write operations ENABLED - can submit reviews and comments")
        else:
            lines.append("Read-only access (write operations disabled)")
        return "\n".join(lines)

    def _build_tools(self) -> list[ToolDefinition]:
        """Build custom GitHub tools."""
        tools = [
            ToolDefinition(
                name="GetPRDetails",
                description="Fetch detailed information about a GitHub pull request",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "pr_number": {
                            "type": "integer",
                            "description": "Pull request number to fetch",
                        },
                    },
                    "required": ["pr_number"],
                },
                handler=self._handle_get_pr_details,
            ),
        ]

        if self.allow_write_operations:
            tools.extend(
                [
                    ToolDefinition(
                        name="SubmitPRReview",
                        description="Submit a review on a pull request",
                        parameters_schema={
                            "type": "object",
                            "properties": {
                                "pr_number": {
                                    "type": "integer",
                                    "description": "Pull request number",
                                },
                                "state": {
                                    "type": "string",
                                    "enum": ["APPROVE", "REQUEST_CHANGES", "COMMENT"],
                                    "description": "Review decision",
                                },
                                "body": {
                                    "type": "string",
                                    "description": "Review body/summary",
                                },
                            },
                            "required": ["pr_number", "state", "body"],
                        },
                        handler=self._handle_submit_review,
                    ),
                    ToolDefinition(
                        name="PostPRComment",
                        description="Post a comment on a pull request",
                        parameters_schema={
                            "type": "object",
                            "properties": {
                                "pr_number": {
                                    "type": "integer",
                                    "description": "Pull request number",
                                },
                                "body": {
                                    "type": "string",
                                    "description": "Comment body",
                                },
                                "path": {
                                    "type": "string",
                                    "description": "File path for line comment (optional)",
                                },
                                "line": {
                                    "type": "integer",
                                    "description": "Line number for line comment (optional)",
                                },
                            },
                            "required": ["pr_number", "body"],
                        },
                        handler=self._handle_post_comment,
                    ),
                ]
            )

        return tools

    def _handle_get_pr_details(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle GetPRDetails tool call."""
        pr_number = params["pr_number"]
        details = get_pr_details(pr_number, self.repo, self.token)
        return details.model_dump()

    def _handle_submit_review(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle SubmitPRReview tool call (mock implementation)."""
        return {
            "status": "submitted",
            "pr_number": params["pr_number"],
            "state": params["state"],
            "body": params["body"],
        }

    def _handle_post_comment(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle PostPRComment tool call (mock implementation)."""
        return {
            "status": "posted",
            "pr_number": params["pr_number"],
            "body": params["body"],
            "path": params.get("path"),
            "line": params.get("line"),
        }

    def _make_validator(self) -> Callable[[ToolCall], ValidationResult]:
        """Create validator for GitHub tools."""

        def validate(tool: ToolCall) -> ValidationResult:
            if tool.name in ("SubmitPRReview", "PostPRComment") and not self.allow_write_operations:
                return ValidationResult.reject(tool, "Write operations not allowed on this binding")

            return ValidationResult.allow(tool)

        return validate

    # === v2 API ===

    def extract_effects(
        self,
        sandbox: Sandbox | None,
        result: ExecutionResult,
    ) -> Sequence[Effect]:
        """Extract GitHub operations as effects from tool calls.

        Parses the execution result to identify SubmitPRReview and PostPRComment
        tool calls, emitting corresponding audit effects.

        Args:
            sandbox: Not used (GitHub context has no filesystem operations)
            result: Execution result containing tool calls

        Returns:
            Sequence of PRReviewSubmitted and PRCommented effects
        """
        effects: list[Effect] = []
        seen_reviews: set[tuple[int, str, str]] = set()
        seen_comments: set[tuple[int, str, str | None, int | None]] = set()

        for call, res in zip(result.tool_calls, result.tool_results, strict=False):
            if not res.success:
                continue

            if call.name == "SubmitPRReview":
                pr_number = call.params.get("pr_number", 0)
                state = call.params.get("state", "")
                body = call.params.get("body", "")
                review_key = (pr_number, state, body)

                if review_key not in seen_reviews:
                    seen_reviews.add(review_key)
                    effects.append(
                        PRReviewSubmitted(
                            pr_number=pr_number,
                            repo=self.repo or "",
                            state=state,
                            body=body,
                            context_id=self.context_id,
                        )
                    )

            elif call.name == "PostPRComment":
                pr_number = call.params.get("pr_number", 0)
                body = call.params.get("body", "")
                path = call.params.get("path")
                line = call.params.get("line")
                comment_key = (pr_number, body, path, line)

                if comment_key not in seen_comments:
                    seen_comments.add(comment_key)
                    effects.append(
                        PRCommented(
                            pr_number=pr_number,
                            repo=self.repo or "",
                            body=body,
                            path=path,
                            line=line,
                            context_id=self.context_id,
                        )
                    )

        return effects

    def apply_effect(self, effect: Effect) -> Self:
        """Apply effect to derive new context state.

        GitHubContext configuration doesn't change based on operations -
        PRReviewSubmitted and PRCommented are audit effects only.

        Args:
            effect: The effect to apply

        Returns:
            Self unchanged (audit effects don't modify context config)
        """
        return self


__all__ = ["GitHubContext"]
