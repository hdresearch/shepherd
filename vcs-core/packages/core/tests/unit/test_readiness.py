from __future__ import annotations

from pathlib import Path

from vcs_core import readiness
from vcs_core.readiness import query_readiness_json, revalidate_readiness_json
from vcs_core.store import Store


def test_readiness_helper_returns_named_readiness_envelope(workspace) -> None:  # type: ignore[no-untyped-def]
    store = Store(str(workspace / ".vcscore"))
    store.create_root_commit()

    payload = query_readiness_json(workspace, {"command": "shepherd.status"})

    assert payload["schema"] == "vcscore/shepherd-query-readiness/v1"
    assert payload["readiness"]["command"] == "shepherd.status"
    assert payload["readiness"]["allowed"] is True


def test_readiness_helper_uses_locked_path_for_mutating_readiness(mg) -> None:  # type: ignore[no-untyped-def]
    mg.exec("filesystem", "write", scope=mg.ground, path="ready.txt", content=b"ready")

    payload = query_readiness_json(
        mg._repo_path,
        {
            "command": "shepherd.run",
            "requested_freshness": "revalidated",
            "allow_best_effort": False,
        },
    )

    assert payload["readiness"]["allowed"] is True
    assert payload["readiness"]["freshness"] == "locked"
    assert payload["mutation_precondition"]["mode"] == "locked"


def test_readiness_helper_revalidates_mutation_precondition(mg) -> None:  # type: ignore[no-untyped-def]
    mg.exec("filesystem", "write", scope=mg.ground, path="ready.txt", content=b"ready")
    request = {
        "command": "shepherd.run",
        "requested_freshness": "locked",
        "allow_best_effort": False,
    }
    payload = query_readiness_json(mg._repo_path, request)

    revalidated = revalidate_readiness_json(mg._repo_path, request, payload["mutation_precondition"])

    assert revalidated["readiness"]["allowed"] is True
    assert revalidated["readiness"]["freshness"] == "revalidated"


def test_readiness_helper_discovers_parent_vcscore_from_nested_workspace(mg) -> None:  # type: ignore[no-untyped-def]
    mg.exec("filesystem", "write", scope=mg.ground, path="ready.txt", content=b"ready")
    nested = Path(mg._workspace) / "src" / "pkg"
    nested.mkdir(parents=True)
    request = {
        "command": "shepherd.run",
        "requested_freshness": "locked",
        "allow_best_effort": False,
    }

    payload = query_readiness_json(nested, request)
    revalidated = revalidate_readiness_json(nested, request, payload["mutation_precondition"])

    assert payload["repository"]["path"] == str(mg._repo_path)
    assert payload["readiness"]["allowed"] is True
    assert revalidated["readiness"]["freshness"] == "revalidated"


def test_readiness_helper_routes_through_live_session(workspace, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = Store(str(workspace / ".vcscore"))
    store.create_root_commit()
    info = object()
    calls: list[tuple[object, str, dict[str, object]]] = []

    def live_session_info(repo_path: str):  # type: ignore[no-untyped-def]
        assert repo_path == str(workspace / ".vcscore")
        return info

    def send_session_request(session_info, method, params):  # type: ignore[no-untyped-def]
        calls.append((session_info, method, params))
        return {
            "ok": True,
            "result": {
                "schema": "vcscore/shepherd-query-readiness/v1",
                "readiness": {
                    "command": "shepherd.status",
                    "allowed": True,
                    "state": "safe_to_run",
                    "admission_authoritative": True,
                    "freshness": "best_effort",
                },
            },
        }

    monkeypatch.setattr(readiness._cli_ipc, "live_session_info", live_session_info)
    monkeypatch.setattr(readiness._cli_ipc, "send_session_request", send_session_request)

    payload = query_readiness_json(workspace, {"command": "shepherd.status"})

    assert payload["readiness"]["command"] == "shepherd.status"
    assert calls[0][0] is info
    assert calls[0][1] == "query_readiness"
    assert calls[0][2]["schema"] == "vcscore/shepherd-query-readiness-request/v1"


def test_readiness_helper_revalidates_through_live_session(workspace, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = Store(str(workspace / ".vcscore"))
    store.create_root_commit()
    info = object()
    calls: list[tuple[object, str, dict[str, object]]] = []

    def live_session_info(repo_path: str):  # type: ignore[no-untyped-def]
        assert repo_path == str(workspace / ".vcscore")
        return info

    def send_session_request(session_info, method, params):  # type: ignore[no-untyped-def]
        calls.append((session_info, method, params))
        return {
            "ok": True,
            "result": {
                "schema": "vcscore/shepherd-query-readiness/v1",
                "readiness": {
                    "command": "shepherd.run",
                    "allowed": True,
                    "state": "safe_to_run",
                    "admission_authoritative": True,
                    "freshness": "revalidated",
                },
                "mutation_precondition": {"schema": "vcscore/mutation-precondition/v1"},
            },
        }

    monkeypatch.setattr(readiness._cli_ipc, "live_session_info", live_session_info)
    monkeypatch.setattr(readiness._cli_ipc, "send_session_request", send_session_request)

    payload = revalidate_readiness_json(
        workspace,
        {"command": "shepherd.run"},
        {"schema": "vcscore/mutation-precondition/v1"},
    )

    assert payload["readiness"]["freshness"] == "revalidated"
    assert calls[0][0] is info
    assert calls[0][1] == "revalidate_readiness"
    assert calls[0][2]["request"]["schema"] == "vcscore/shepherd-query-readiness-request/v1"
    assert calls[0][2]["precondition"]["schema"] == "vcscore/mutation-precondition/v1"
