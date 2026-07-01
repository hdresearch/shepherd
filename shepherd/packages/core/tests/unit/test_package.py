"""Tests for the @package decorator and PackageInfo registry."""

from __future__ import annotations

from shepherd_core.package import _REGISTRY, PackageInfo, get_package_registry, package


class TestPackageDecorator:
    """Tests for the @package decorator."""

    def setup_method(self) -> None:
        """Clear the registry before each test."""
        self._saved = dict(_REGISTRY)
        _REGISTRY.clear()

    def teardown_method(self) -> None:
        """Restore the registry after each test."""
        _REGISTRY.clear()
        _REGISTRY.update(self._saved)

    def test_basic_registration(self) -> None:
        @package(
            name="test-pkg",
            version="1.0.0",
            tasks=["test_pkg.tasks"],
        )
        def test_pkg() -> None:
            """A test package."""

        assert "test-pkg" in _REGISTRY
        info = _REGISTRY["test-pkg"]
        assert info.name == "test-pkg"
        assert info.version == "1.0.0"
        assert info.doc == "A test package."
        assert info.task_modules == ("test_pkg.tasks",)
        assert info.context_modules == ()
        assert info.effect_modules == ()
        assert info.requires == ()

    def test_full_registration(self) -> None:
        @package(
            name="full-pkg",
            version="2.0.0",
            tasks=["full_pkg.tasks", "full_pkg.workflows"],
            contexts=["full_pkg.contexts"],
            effects=["full_pkg.effects"],
            requires=["other-pkg>=1.0"],
        )
        def full_pkg() -> None:
            """Full package with all fields."""

        info = _REGISTRY["full-pkg"]
        assert info.task_modules == ("full_pkg.tasks", "full_pkg.workflows")
        assert info.context_modules == ("full_pkg.contexts",)
        assert info.effect_modules == ("full_pkg.effects",)
        assert info.requires == ("other-pkg>=1.0",)

    def test_attaches_package_info_to_function(self) -> None:
        @package(
            name="attr-pkg",
            version="0.1.0",
            tasks=["attr_pkg.tasks"],
        )
        def attr_pkg() -> None:
            """Test attribute attachment."""

        assert hasattr(attr_pkg, "_package_info")
        assert isinstance(attr_pkg._package_info, PackageInfo)
        assert attr_pkg._package_info.name == "attr-pkg"

    def test_function_is_returned_unchanged(self) -> None:
        @package(
            name="identity-pkg",
            version="0.1.0",
            tasks=["identity_pkg.tasks"],
        )
        def identity_pkg() -> str:
            """Returns a value."""
            return "hello"

        assert identity_pkg() == "hello"

    def test_no_docstring(self) -> None:
        @package(
            name="nodoc-pkg",
            version="0.1.0",
            tasks=["nodoc_pkg.tasks"],
        )
        def nodoc_pkg() -> None:
            pass

        assert _REGISTRY["nodoc-pkg"].doc == ""

    def test_get_package_registry_returns_copy(self) -> None:
        @package(
            name="copy-pkg",
            version="0.1.0",
            tasks=["copy_pkg.tasks"],
        )
        def copy_pkg() -> None:
            """Test copy."""

        registry = get_package_registry()
        assert "copy-pkg" in registry
        # Mutating the copy should not affect the original
        registry.pop("copy-pkg")
        assert "copy-pkg" in _REGISTRY

    def test_package_info_is_frozen(self) -> None:
        info = PackageInfo(
            name="frozen",
            version="1.0.0",
            doc="test",
            task_modules=("a",),
            context_modules=(),
            effect_modules=(),
            requires=(),
        )
        try:
            info.name = "mutated"  # type: ignore[misc]
            raise AssertionError("Should have raised FrozenInstanceError")
        except AttributeError:
            pass  # Expected — dataclass is frozen

    def test_overwrite_existing_registration(self) -> None:
        @package(name="dup", version="1.0", tasks=["a"])
        def dup_v1() -> None:
            """Version 1."""

        @package(name="dup", version="2.0", tasks=["b"])
        def dup_v2() -> None:
            """Version 2."""

        assert _REGISTRY["dup"].version == "2.0"
        assert _REGISTRY["dup"].task_modules == ("b",)
