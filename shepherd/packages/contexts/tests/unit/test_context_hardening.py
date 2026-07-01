"""Tests for context hardening fixes (Stream B).

These tests verify fixes for:
1. KVStore isinstance() guards (Issue 2.1)
2. MCP env expansion silent failure (Issue 2.1)
3. Database bounds checking (Issue 4.1)
"""

import os
import warnings

import pytest
from shepherd_contexts.database.context import DatabaseContext
from shepherd_contexts.kvstore.effects import KeyDeleted, KeySet
from shepherd_contexts.kvstore.store import KVStoreContext
from shepherd_contexts.mcp.server import MCPServerContext
from shepherd_core.effects import Effect


class TestKVStoreInstanceofGuards:
    """Test that KVStore properly handles effects without task_name attribute."""

    def test_key_set_without_task_name_attribute(self):
        """KeySet effect without task_name should not raise AttributeError."""
        store = KVStoreContext(data={"existing": "value"})

        # Create effect without task_name attribute
        effect = KeySet(
            key="new_key",
            new_value="new_value",
            context_id=store.context_id,
        )

        # This should not raise AttributeError
        new_store = store.apply_effect(effect)

        assert new_store.data["new_key"] == "new_value"
        assert new_store.source_step is None  # No task_name, so should be None

    def test_key_deleted_without_task_name_attribute(self):
        """KeyDeleted effect without task_name should not raise AttributeError."""
        store = KVStoreContext(data={"to_delete": "value", "keep": "this"})

        # Create effect without task_name attribute
        effect = KeyDeleted(
            key="to_delete",
            context_id=store.context_id,
        )

        # This should not raise AttributeError
        new_store = store.apply_effect(effect)

        assert "to_delete" not in new_store.data
        assert new_store.source_step is None  # No task_name, so should be None

    def test_key_set_with_task_name_attribute(self):
        """KeySet effect with task_name should preserve it."""
        store = KVStoreContext(data={})

        # Create effect with task_name attribute
        effect = KeySet(
            key="test",
            new_value="test_value",
            context_id=store.context_id,
        )
        # Manually add task_name attribute
        object.__setattr__(effect, "task_name", "test_task")

        new_store = store.apply_effect(effect)

        assert new_store.data["test"] == "test_value"
        assert new_store.source_step == "test_task"

    def test_key_deleted_with_task_name_attribute(self):
        """KeyDeleted effect with task_name should preserve it."""
        store = KVStoreContext(data={"key": "value"})

        # Create effect with task_name attribute
        effect = KeyDeleted(
            key="key",
            context_id=store.context_id,
        )
        # Manually add task_name attribute
        object.__setattr__(effect, "task_name", "delete_task")

        new_store = store.apply_effect(effect)

        assert "key" not in new_store.data
        assert new_store.source_step == "delete_task"

    def test_unrelated_effect_without_task_name(self):
        """Unrelated effect without task_name should be ignored gracefully."""
        store = KVStoreContext(data={"existing": "value"})

        # Create a generic effect (not KeySet or KeyDeleted)
        class OtherEffect(Effect):
            effect_type: str = "other"

        effect = OtherEffect(context_id="other:context")

        # Should return self unchanged, no AttributeError
        new_store = store.apply_effect(effect)

        assert new_store is store


class TestMCPEnvExpansionWarnings:
    """Test that MCP server warns when environment variables are missing."""

    def test_missing_env_var_without_default_warns(self):
        """Missing env var without default should warn and return empty string."""
        # Ensure env var doesn't exist
        env_var_name = "TEST_MISSING_VAR_12345"
        if env_var_name in os.environ:
            del os.environ[env_var_name]

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            server = MCPServerContext(name="test", command="test", env={"KEY": f"${{{env_var_name}}}"})
            config = server._build_transport_config()

            # Should have issued a warning
            assert len(w) == 1
            assert issubclass(w[0].category, UserWarning)
            assert env_var_name in str(w[0].message)
            assert "not set" in str(w[0].message)

            # Should return empty string
            assert config["env"]["KEY"] == ""

    def test_missing_env_var_with_default_no_warning(self):
        """Missing env var with default should not warn and return default."""
        # Ensure env var doesn't exist
        env_var_name = "TEST_MISSING_VAR_23456"
        if env_var_name in os.environ:
            del os.environ[env_var_name]

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            server = MCPServerContext(name="test", command="test", env={"KEY": f"${{{env_var_name}:-default_value}}"})
            config = server._build_transport_config()

            # Should NOT have issued a warning
            assert len(w) == 0

            # Should return default value
            assert config["env"]["KEY"] == "default_value"

    def test_existing_env_var_no_warning(self):
        """Existing env var should expand correctly without warning."""
        # Set up test env var
        env_var_name = "TEST_EXISTING_VAR_34567"
        os.environ[env_var_name] = "test_value"

        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")

                server = MCPServerContext(name="test", command="test", env={"KEY": f"${{{env_var_name}}}"})
                config = server._build_transport_config()

                # Should NOT have issued a warning
                assert len(w) == 0

                # Should return actual value
                assert config["env"]["KEY"] == "test_value"
        finally:
            # Clean up
            if env_var_name in os.environ:
                del os.environ[env_var_name]

    def test_empty_default_no_warning(self):
        """Missing env var with empty default should not warn."""
        # Ensure env var doesn't exist
        env_var_name = "TEST_MISSING_VAR_45678"
        if env_var_name in os.environ:
            del os.environ[env_var_name]

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            server = MCPServerContext(name="test", command="test", env={"KEY": f"${{{env_var_name}:-}}"})
            config = server._build_transport_config()

            # Should NOT have issued a warning (empty default is explicit)
            assert len(w) == 0

            # Should return empty string
            assert config["env"]["KEY"] == ""

    def test_url_expansion_warns_on_missing_var(self):
        """URL expansion should also warn on missing env vars."""
        # Ensure env var doesn't exist
        env_var_name = "TEST_MISSING_URL_VAR"
        if env_var_name in os.environ:
            del os.environ[env_var_name]

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            server = MCPServerContext(
                name="test", url=f"https://example.com/${{{env_var_name}}}/endpoint", transport_type="sse"
            )
            config = server._build_transport_config()

            # Should have issued a warning
            assert len(w) == 1
            assert issubclass(w[0].category, UserWarning)
            assert env_var_name in str(w[0].message)

            # URL should have empty string substituted
            assert config["url"] == "https://example.com//endpoint"

    def test_headers_expansion_warns_on_missing_var(self):
        """Headers expansion should also warn on missing env vars."""
        # Ensure env var doesn't exist
        env_var_name = "TEST_MISSING_HEADER_VAR"
        if env_var_name in os.environ:
            del os.environ[env_var_name]

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            server = MCPServerContext(
                name="test",
                url="https://example.com",
                headers={"Authorization": f"Bearer ${{{env_var_name}}}"},
                transport_type="sse",
            )
            config = server._build_transport_config()

            # Should have issued a warning
            assert len(w) == 1
            assert issubclass(w[0].category, UserWarning)
            assert env_var_name in str(w[0].message)

            # Header should have empty string substituted
            assert config["headers"]["Authorization"] == "Bearer "


class TestDatabaseBoundsChecking:
    """Test that DatabaseContext validates bounds properly."""

    def test_negative_max_rows_raises_error(self):
        """Creating DatabaseContext with negative max_rows should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            DatabaseContext(connection_string="sqlite:///:memory:", database_name="test_db", max_rows=-1)

        assert "max_rows must be non-negative" in str(exc_info.value)
        assert "-1" in str(exc_info.value)

    def test_zero_max_rows_allowed(self):
        """Zero max_rows should be allowed (might mean no limit or return nothing)."""
        # Should not raise
        db = DatabaseContext(connection_string="sqlite:///:memory:", database_name="test_db", max_rows=0)

        assert db.max_rows == 0

    def test_positive_max_rows_allowed(self):
        """Positive max_rows should be allowed."""
        # Should not raise
        db = DatabaseContext(connection_string="sqlite:///:memory:", database_name="test_db", max_rows=100)

        assert db.max_rows == 100

    def test_default_max_rows_positive(self):
        """Default max_rows should be positive."""
        db = DatabaseContext(connection_string="sqlite:///:memory:", database_name="test_db")

        assert db.max_rows > 0

    def test_large_negative_max_rows_raises_error(self):
        """Large negative max_rows should also raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            DatabaseContext(connection_string="sqlite:///:memory:", database_name="test_db", max_rows=-999999)

        assert "max_rows must be non-negative" in str(exc_info.value)
        assert "-999999" in str(exc_info.value)


class TestIntegrationScenarios:
    """Integration tests combining multiple hardening fixes."""

    def test_kvstore_handles_effect_chain_without_task_names(self):
        """KVStore should handle a chain of effects without task_name attributes."""
        store = KVStoreContext.create({"initial": "value"})

        effects = [
            KeySet(key="key1", new_value="value1", context_id=store.context_id),
            KeySet(key="key2", new_value="value2", context_id=store.context_id),
            KeyDeleted(key="initial", context_id=store.context_id),
        ]

        new_store = store
        for effect in effects:
            new_store = new_store.apply_effect(effect)

        assert new_store.data == {"key1": "value1", "key2": "value2"}
        assert new_store.source_step is None

    def test_mcp_multiple_missing_vars_produces_multiple_warnings(self):
        """Multiple missing env vars should produce multiple warnings."""
        # Ensure env vars don't exist
        var1 = "TEST_MISSING_VAR_A"
        var2 = "TEST_MISSING_VAR_B"
        for var in [var1, var2]:
            if var in os.environ:
                del os.environ[var]

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            server = MCPServerContext(
                name="test",
                url=f"https://${{{var1}}}.example.com",
                headers={"Auth": f"Bearer ${{{var2}}}"},
                transport_type="sse",
            )
            config = server._build_transport_config()

            # Should have two warnings (one per missing var)
            assert len(w) == 2
            warning_messages = [str(warning.message) for warning in w]
            assert any(var1 in msg for msg in warning_messages)
            assert any(var2 in msg for msg in warning_messages)

    def test_database_description_includes_valid_max_rows(self):
        """Database description should include the valid max_rows value."""
        db = DatabaseContext(connection_string="sqlite:///:memory:", database_name="test_db", max_rows=500)

        description = db._build_description()
        assert "500" in description
        assert "Max rows" in description
