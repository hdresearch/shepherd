"""Tests for scope message extraction functionality.

Tests the message extraction API:
1. Scope.get_messages() - Get messages from a scope's stream
2. Filtering by task_name and provider
"""

from shepherd_core.effects import (
    AgentMessage,
    AgentThinking,
    PromptSent,
)
from shepherd_runtime.scope import Scope


class TestScopeGetMessages:
    """Test Scope.get_messages() method."""

    def test_get_messages_empty_stream(self):
        """get_messages() on empty stream returns empty list."""
        with Scope(root=True) as scope:
            messages = scope.get_messages()
            assert messages == []

    def test_get_messages_extracts_user_prompts(self):
        """get_messages() extracts user role from PromptSent."""
        with Scope(root=True) as scope:
            scope.emit(
                PromptSent(
                    user_prompt="Hello, world!",
                    system_prompt="You are helpful.",
                    total_tokens=10,
                )
            )

            messages = scope.get_messages()

            assert len(messages) == 1
            assert messages[0]["role"] == "user"
            assert messages[0]["content"] == "Hello, world!"

    def test_get_messages_extracts_assistant_messages(self):
        """get_messages() extracts assistant role from AgentMessage."""
        with Scope(root=True) as scope:
            scope.emit(AgentMessage(content="I can help with that!"))

            messages = scope.get_messages()

            assert len(messages) == 1
            assert messages[0]["role"] == "assistant"
            assert messages[0]["content"] == "I can help with that!"

    def test_get_messages_extracts_thinking(self):
        """get_messages() extracts thinking role from AgentThinking."""
        with Scope(root=True) as scope:
            scope.emit(AgentThinking(content="Let me think about this..."))

            messages = scope.get_messages()

            assert len(messages) == 1
            assert messages[0]["role"] == "thinking"
            assert messages[0]["content"] == "Let me think about this..."

    def test_get_messages_skips_partial_messages(self):
        """get_messages() skips partial (streaming) messages."""
        with Scope(root=True) as scope:
            # Partial message (streaming delta)
            scope.emit(AgentMessage(content="Hello", is_partial=True))
            # Complete message
            scope.emit(AgentMessage(content="Hello, world!"))

            messages = scope.get_messages()

            assert len(messages) == 1
            assert messages[0]["content"] == "Hello, world!"

    def test_get_messages_skips_empty_content(self):
        """get_messages() skips messages with empty content."""
        with Scope(root=True) as scope:
            scope.emit(PromptSent(user_prompt="", total_tokens=0))
            scope.emit(AgentMessage(content=""))
            scope.emit(AgentThinking(content=""))
            scope.emit(AgentMessage(content="Real message"))

            messages = scope.get_messages()

            assert len(messages) == 1
            assert messages[0]["content"] == "Real message"

    def test_get_messages_preserves_order(self):
        """get_messages() preserves chronological order."""
        with Scope(root=True) as scope:
            scope.emit(PromptSent(user_prompt="What is 2+2?", total_tokens=5))
            scope.emit(AgentThinking(content="Simple arithmetic..."))
            scope.emit(AgentMessage(content="The answer is 4."))

            messages = scope.get_messages()

            assert len(messages) == 3
            assert messages[0]["role"] == "user"
            assert messages[1]["role"] == "thinking"
            assert messages[2]["role"] == "assistant"

    def test_get_messages_includes_task_attribution(self):
        """get_messages() includes task name in each message."""
        with Scope(root=True) as scope:
            # Emit with task attribution via with_attribution()
            effect = PromptSent(
                user_prompt="Hello",
                total_tokens=5,
            ).with_attribution(task_name="MyTask")
            scope.emit(effect)

            messages = scope.get_messages()

            assert len(messages) == 1
            assert messages[0]["task"] == "MyTask"


class TestScopeGetMessagesFiltering:
    """Test filtering capabilities of Scope.get_messages()."""

    def test_filter_by_task_name(self):
        """get_messages(task_name=...) filters by task."""
        with Scope(root=True) as scope:
            # Task 1 messages
            scope.emit(PromptSent(user_prompt="Task 1 prompt", total_tokens=5).with_attribution(task_name="Task1"))
            scope.emit(AgentMessage(content="Task 1 response").with_attribution(task_name="Task1"))
            # Task 2 messages
            scope.emit(PromptSent(user_prompt="Task 2 prompt", total_tokens=5).with_attribution(task_name="Task2"))
            scope.emit(AgentMessage(content="Task 2 response").with_attribution(task_name="Task2"))

            task1_messages = scope.get_messages(task_name="Task1")
            task2_messages = scope.get_messages(task_name="Task2")
            all_messages = scope.get_messages()

            assert len(task1_messages) == 2
            assert len(task2_messages) == 2
            assert len(all_messages) == 4
            assert all(m["task"] == "Task1" for m in task1_messages)
            assert all(m["task"] == "Task2" for m in task2_messages)

    def test_filter_by_provider_id(self):
        """get_messages(provider=...) filters by provider_id."""
        with Scope(root=True) as scope:
            # Provider 1 messages
            scope.emit(
                PromptSent(user_prompt="Claude prompt", total_tokens=5).with_attribution(
                    provider_id="provider:claude:abc"
                )
            )
            scope.emit(AgentMessage(content="Claude response").with_attribution(provider_id="provider:claude:abc"))
            # Provider 2 messages
            scope.emit(
                PromptSent(user_prompt="OpenAI prompt", total_tokens=5).with_attribution(
                    provider_id="provider:openai:def"
                )
            )
            scope.emit(AgentMessage(content="OpenAI response").with_attribution(provider_id="provider:openai:def"))

            claude_messages = scope.get_messages(provider="provider:claude:abc")
            openai_messages = scope.get_messages(provider="provider:openai:def")
            all_messages = scope.get_messages()

            assert len(claude_messages) == 2
            assert len(openai_messages) == 2
            assert len(all_messages) == 4

    def test_filter_by_task_and_provider(self):
        """get_messages() can filter by both task and provider."""
        with Scope(root=True) as scope:
            # Task1 with Claude
            scope.emit(
                AgentMessage(content="T1 Claude").with_attribution(
                    task_name="Task1",
                    provider_id="provider:claude:abc",
                )
            )
            # Task1 with OpenAI
            scope.emit(
                AgentMessage(content="T1 OpenAI").with_attribution(
                    task_name="Task1",
                    provider_id="provider:openai:def",
                )
            )
            # Task2 with Claude
            scope.emit(
                AgentMessage(content="T2 Claude").with_attribution(
                    task_name="Task2",
                    provider_id="provider:claude:abc",
                )
            )

            # Filter by task only
            task1_messages = scope.get_messages(task_name="Task1")
            assert len(task1_messages) == 2

            # Filter by provider only
            claude_messages = scope.get_messages(provider="provider:claude:abc")
            assert len(claude_messages) == 2

            # Filter by both
            task1_claude = scope.get_messages(task_name="Task1", provider="provider:claude:abc")
            assert len(task1_claude) == 1
            assert task1_claude[0]["content"] == "T1 Claude"


class TestNestedScopeMessages:
    """Test get_messages() with nested scopes."""

    def test_nested_scope_has_own_messages(self):
        """Nested scope's get_messages() only returns its own messages."""
        with Scope(root=True) as parent:
            parent.emit(AgentMessage(content="Parent message"))

            with parent.child() as child:
                child.emit(AgentMessage(content="Child message"))

                parent_messages = parent.get_messages()
                child_messages = child.get_messages()

                # Child only sees its own messages
                assert len(child_messages) == 1
                assert child_messages[0]["content"] == "Child message"

                # Parent sees all messages (due to effect propagation)
                assert len(parent_messages) == 2

    def test_effect_propagation_to_parent(self):
        """Effects from child scope propagate to parent's stream."""
        with Scope(root=True) as parent:
            with parent.child() as child:
                child.emit(AgentMessage(content="From child"))

            # After child exits, parent has the message
            messages = parent.get_messages()
            assert len(messages) == 1
            assert messages[0]["content"] == "From child"


class TestModuleLevelGetMessagesRemoved:
    """The hard-cut facade no longer exposes module-level global-scope helpers."""

    def test_module_level_get_messages_is_not_top_level(self):
        import shepherd

        assert not hasattr(shepherd, "get_messages")
        assert "get_messages" not in shepherd.__all__
