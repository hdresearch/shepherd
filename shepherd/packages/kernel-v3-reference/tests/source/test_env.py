import pytest

from shepherd_kernel_v3_reference.source.values import Env


def test_empty_env_lookup_raises() -> None:
    env = Env()
    with pytest.raises(KeyError):
        env.lookup("x")


def test_extend_returns_new_env_without_mutating_original() -> None:
    env = Env()
    env2 = env.extend("x", 1)
    assert "x" not in env
    assert "x" in env2
    assert env2.lookup("x") == 1


def test_later_bindings_shadow_earlier() -> None:
    env = Env().extend("x", 1).extend("x", 2)
    assert env.lookup("x") == 2


def test_extension_preserves_immutability() -> None:
    env = Env().extend("x", 1)
    env2 = env.extend("y", 2)
    assert "y" not in env
    assert "y" in env2
    assert env.lookup("x") == 1
    assert env2.lookup("x") == 1


def test_bindings_materializes_parent_pointer_env_in_insertion_order() -> None:
    env = Env().extend("x", 1).extend("y", 2).extend("x", 3)

    assert env.bindings == (("x", 1), ("y", 2), ("x", 3))
    assert Env(env.bindings) == env


def test_extend_does_not_materialize_parent_bindings() -> None:
    env = Env()
    for idx in range(20):
        env = env.extend(f"x{idx}", idx)

    assert env._bindings_cache is None
    assert env.lookup("x0") == 0
    assert env._bindings_cache is None
    assert env.bindings[-1] == ("x19", 19)
