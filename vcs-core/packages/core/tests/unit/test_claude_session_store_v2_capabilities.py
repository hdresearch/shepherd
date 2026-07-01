"""Optional Claude SessionStore capability tests for v2 session substrates."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pygit2
import pytest

session_conformance = pytest.importorskip("claude_agent_sdk.testing.session_store_conformance")

from .world_vectors_v2_helpers import (
    SIG,
)
from .world_vectors_v2_helpers import (
    candidate_ref as _candidate_ref,
)
from .world_vectors_v2_helpers import (
    canonical_bytes as _canonical_bytes,
)
from .world_vectors_v2_helpers import (
    commit_json as _commit_json,
)
from .world_vectors_v2_helpers import (
    read_blob_bytes as _read_blob_bytes,
)
from .world_vectors_v2_helpers import (
    read_json_blob as _read_json_blob,
)
from .world_vectors_v2_helpers import (
    update_ref as _update_ref,
)
from .world_vectors_v2_helpers import (
    validate_candidate_ref as _validate_candidate_ref,
)


def _key_digest(key: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(key)).hexdigest()


class _GitClaudeSessionStore:
    def __init__(self, repo: pygit2.Repository, *, operation_id: str, binding: str) -> None:
        self.repo = repo
        self.operation_id = operation_id
        self.binding = binding
        self._last_mtime = 1_700_000_000_000

    async def append(self, key: dict[str, Any], entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        digest = _key_digest(key)
        ref = self._key_ref(digest)
        previous = self._target_for_ref(ref)
        parents = [] if previous is None else [previous]
        batch_index = 0 if previous is None else self._read_batch_index(previous) + 1
        payload = self._write_append_tree(key, entries, batch_index=batch_index, mtime_ms=self._next_mtime())
        oid = self.repo.create_commit(None, SIG, SIG, f"append {digest}", payload, parents)
        _update_ref(self.repo, ref, oid)

    async def load(self, key: dict[str, Any]) -> list[dict[str, Any]] | None:
        digest = _key_digest(key)
        ref = self._key_ref(digest)
        oid = self._target_for_ref(ref)
        if oid is None:
            return None

        entries: list[dict[str, Any]] = []
        for commit in reversed(self._transcript_chain(oid)):
            payload = json.loads(_read_blob_bytes(self.repo, commit.tree, "payload/entries.json").decode("utf-8"))
            entries.extend(payload)
        return entries

    async def list_sessions(self, project_key: str) -> list[dict[str, Any]]:
        sessions = []
        for _, key, batch in self._iter_transcript_heads():
            if key.get("project_key") == project_key and "subpath" not in key:
                sessions.append({"session_id": str(key["session_id"]), "mtime": batch["mtime_ms"]})
        return sorted(sessions, key=lambda session: session["session_id"])

    async def list_subkeys(self, key: dict[str, Any]) -> list[str]:
        return self._subkey_paths(str(key["project_key"]), str(key["session_id"]))

    def transcript_head(self, key: dict[str, Any]) -> pygit2.Oid:
        return self.repo.references[self._key_ref(_key_digest(key))].target

    def finalize_session_revision(
        self,
        *,
        provider_session_id: str,
        project_key: str,
        parent_provider_session_id: str | None,
        branch_kind: str,
    ) -> pygit2.Oid:
        main_key = {"project_key": project_key, "session_id": provider_session_id}
        main_digest = _key_digest(main_key)
        main_head = self.transcript_head(main_key)
        subagent_transcripts = {}
        for subpath in self._subkey_paths(project_key, provider_session_id):
            subkey = {**main_key, "subpath": subpath}
            sub_digest = _key_digest(subkey)
            subagent_transcripts[subpath] = {"key_digest": sub_digest, "head": str(self.transcript_head(subkey))}

        return _commit_json(
            self.repo,
            _candidate_ref(self.operation_id, self.binding),
            {
                "schema": "vcscore/session-revision/v1",
                "kind": "shepherd.session_state",
                "provider": "claude-code",
                "operation_id": self.operation_id,
                "binding": self.binding,
                "project_key": project_key,
                "provider_session_id": provider_session_id,
                "parent_provider_session_id": parent_provider_session_id,
                "branch_kind": branch_kind,
                "main_transcript": {"key_digest": main_digest, "head": str(main_head)},
                "subagent_transcripts": subagent_transcripts,
            },
        )

    def _write_append_tree(
        self,
        key: dict[str, Any],
        entries: list[dict[str, Any]],
        *,
        batch_index: int,
        mtime_ms: int,
    ) -> pygit2.Oid:
        meta_builder = self.repo.TreeBuilder()
        meta_builder.insert(
            "key.json",
            self.repo.create_blob(json.dumps(key, sort_keys=True, separators=(",", ":")).encode("utf-8")),
            pygit2.GIT_FILEMODE_BLOB,
        )
        meta_builder.insert(
            "batch.json",
            self.repo.create_blob(
                json.dumps(
                    {
                        "schema": "vcscore/claude-transcript-batch/v1",
                        "operation_id": self.operation_id,
                        "binding": self.binding,
                        "key_digest": _key_digest(key),
                        "entry_count": len(entries),
                        "batch_index": batch_index,
                        "mtime_ms": mtime_ms,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
            pygit2.GIT_FILEMODE_BLOB,
        )
        payload_builder = self.repo.TreeBuilder()
        payload_builder.insert(
            "entries.json",
            self.repo.create_blob(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")),
            pygit2.GIT_FILEMODE_BLOB,
        )
        root_builder = self.repo.TreeBuilder()
        root_builder.insert("meta", meta_builder.write(), pygit2.GIT_FILEMODE_TREE)
        root_builder.insert("payload", payload_builder.write(), pygit2.GIT_FILEMODE_TREE)
        return root_builder.write()

    def _target_for_ref(self, ref: str) -> pygit2.Oid | None:
        try:
            return self.repo.references[ref].target
        except KeyError:
            return None

    def _read_batch_index(self, oid: pygit2.Oid) -> int:
        commit = self.repo[oid]
        if not isinstance(commit, pygit2.Commit):
            raise TypeError("transcript ref did not point at a commit")
        batch = _read_json_blob(self.repo, commit.tree, "meta/batch.json")
        return int(batch["batch_index"])

    def _transcript_chain(self, oid: pygit2.Oid) -> list[pygit2.Commit]:
        chain: list[pygit2.Commit] = []
        current: pygit2.Oid | None = oid
        while current is not None:
            commit = self.repo[current]
            if not isinstance(commit, pygit2.Commit):
                raise TypeError("transcript ref did not point at a commit")
            chain.append(commit)
            current = commit.parents[0].id if commit.parents else None
        return chain

    def _iter_transcript_heads(self) -> list[tuple[pygit2.Commit, dict[str, Any], dict[str, Any]]]:
        heads = []
        for ref in sorted(self.repo.references):
            if not ref.startswith(self._key_ref_prefix()):
                continue
            commit = self.repo[self.repo.references[ref].target]
            if not isinstance(commit, pygit2.Commit):
                raise TypeError("transcript ref did not point at a commit")
            key = _read_json_blob(self.repo, commit.tree, "meta/key.json")
            batch = _read_json_blob(self.repo, commit.tree, "meta/batch.json")
            heads.append((commit, key, batch))
        return heads

    def _subkey_paths(self, project_key: str, session_id: str) -> list[str]:
        subpaths = []
        for _, key, _ in self._iter_transcript_heads():
            if key.get("project_key") != project_key or key.get("session_id") != session_id:
                continue
            subpath = key.get("subpath")
            if isinstance(subpath, str):
                subpaths.append(subpath)
        return sorted(subpaths)

    @staticmethod
    def _key_ref(digest: str) -> str:
        return f"{_GitClaudeSessionStore._key_ref_prefix()}{digest}"

    @staticmethod
    def _key_ref_prefix() -> str:
        return "refs/vcscore/session-store/by-key/"

    def _next_mtime(self) -> int:
        now = int(time.time() * 1000)
        self._last_mtime = max(now, self._last_mtime + 1)
        return self._last_mtime


class _FailingGitClaudeSessionStore(_GitClaudeSessionStore):
    async def append(self, key: dict[str, Any], entries: list[dict[str, Any]]) -> None:
        raise RuntimeError("mirror append failed")


def _batch_indexes_for_key(store: _GitClaudeSessionStore, key: dict[str, Any]) -> list[int]:
    return [
        _read_json_blob(store.repo, commit.tree, "meta/batch.json")["batch_index"]
        for commit in reversed(store._transcript_chain(store.transcript_head(key)))
    ]


@pytest.mark.asyncio
@pytest.mark.spike
async def test_git_backed_claude_session_store_passes_sdk_conformance(tmp_path: Path) -> None:
    counter = 0

    def make_store() -> _GitClaudeSessionStore:
        nonlocal counter
        counter += 1
        repo = pygit2.init_repository(str(tmp_path / f"session-{counter}.git"), bare=True)
        return _GitClaudeSessionStore(repo, operation_id=f"op-conformance-{counter}", binding="session")

    await session_conformance.run_session_store_conformance(
        make_store,
        skip_optional=frozenset({"delete", "list_session_summaries"}),
    )


@pytest.mark.asyncio
@pytest.mark.spike
async def test_git_backed_claude_session_store_records_raw_keys_and_session_revision_candidates(tmp_path: Path) -> None:
    repo = pygit2.init_repository(str(tmp_path / "session.git"), bare=True)
    store = _GitClaudeSessionStore(repo, operation_id="op-session-candidate", binding="session")
    main_key = {"project_key": "project-A", "session_id": "claude-session-1"}
    sub_key = {**main_key, "subpath": "subagents/agent-1"}

    await store.append(main_key, [{"type": "user", "uuid": "u1", "message": {"content": "hello"}}])
    await store.append(main_key, [{"type": "assistant", "uuid": "a2", "message": {"content": "world"}}])
    await store.append(main_key, [{"type": "user", "uuid": "u3", "message": {"content": "again"}}])
    await store.append(sub_key, [{"type": "assistant", "uuid": "a1", "message": {"content": "subagent"}}])
    revision = store.finalize_session_revision(
        provider_session_id="claude-session-1",
        project_key="project-A",
        parent_provider_session_id=None,
        branch_kind="fresh",
    )

    _validate_candidate_ref(
        repo,
        operation_id="op-session-candidate",
        binding="session",
        expected_head=revision,
    )
    transcript_commit = repo[store.transcript_head(main_key)]
    raw_key = _read_json_blob(repo, transcript_commit.tree, "meta/key.json")
    revision_payload = _read_json_blob(repo, repo[revision].tree, "revision.json")

    assert raw_key == main_key
    assert await store.load(main_key) == [
        {"type": "user", "uuid": "u1", "message": {"content": "hello"}},
        {"type": "assistant", "uuid": "a2", "message": {"content": "world"}},
        {"type": "user", "uuid": "u3", "message": {"content": "again"}},
    ]
    assert _batch_indexes_for_key(store, main_key) == [0, 1, 2]
    assert await store.list_subkeys(main_key) == ["subagents/agent-1"]
    assert revision_payload["main_transcript"]["head"] == str(store.transcript_head(main_key))
    assert revision_payload["subagent_transcripts"]["subagents/agent-1"]["head"] == str(store.transcript_head(sub_key))


@pytest.mark.asyncio
@pytest.mark.spike
async def test_git_backed_claude_session_store_reconstructs_indexes_after_reopen(tmp_path: Path) -> None:
    repo = pygit2.init_repository(str(tmp_path / "session.git"), bare=True)
    store = _GitClaudeSessionStore(repo, operation_id="op-session-candidate", binding="session")
    main_key = {"project_key": "project-A", "session_id": "claude-session-1"}
    sub_key = {**main_key, "subpath": "subagents/agent-1"}

    await store.append(main_key, [{"type": "user", "uuid": "u1"}])
    await store.append(sub_key, [{"type": "assistant", "uuid": "s1"}])

    reopened_repo = pygit2.Repository(repo.path)
    reopened = _GitClaudeSessionStore(reopened_repo, operation_id="op-session-reopened", binding="session")

    assert await reopened.load(main_key) == [{"type": "user", "uuid": "u1"}]
    assert await reopened.load(sub_key) == [{"type": "assistant", "uuid": "s1"}]
    assert await reopened.list_sessions("project-A") == [
        {
            "session_id": "claude-session-1",
            "mtime": _read_json_blob(repo, repo[store.transcript_head(main_key)].tree, "meta/batch.json")["mtime_ms"],
        }
    ]
    assert await reopened.list_subkeys(main_key) == ["subagents/agent-1"]

    await reopened.append(main_key, [{"type": "assistant", "uuid": "a2"}])
    assert await reopened.load(main_key) == [
        {"type": "user", "uuid": "u1"},
        {"type": "assistant", "uuid": "a2"},
    ]
    assert _batch_indexes_for_key(reopened, main_key) == [0, 1]

    revision = reopened.finalize_session_revision(
        provider_session_id="claude-session-1",
        project_key="project-A",
        parent_provider_session_id=None,
        branch_kind="reopened",
    )
    _validate_candidate_ref(
        reopened_repo,
        operation_id="op-session-reopened",
        binding="session",
        expected_head=revision,
    )
    revision_payload = _read_json_blob(reopened_repo, reopened_repo[revision].tree, "revision.json")
    assert revision_payload["main_transcript"]["head"] == str(reopened.transcript_head(main_key))
    assert revision_payload["subagent_transcripts"]["subagents/agent-1"]["head"] == str(
        reopened.transcript_head(sub_key)
    )


@pytest.mark.asyncio
@pytest.mark.spike
async def test_strict_claude_mirror_failure_refuses_session_candidate_publication(tmp_path: Path) -> None:
    repo = pygit2.init_repository(str(tmp_path / "session.git"), bare=True)
    store = _FailingGitClaudeSessionStore(repo, operation_id="op-mirror-failure", binding="session")
    expected = pygit2.Oid(hex="1" * 40)

    with pytest.raises(RuntimeError, match="mirror append failed"):
        await store.append({"project_key": "project-A", "session_id": "claude-session-1"}, [{"type": "user"}])

    with pytest.raises(ValueError, match="without a durable candidate ref"):
        _validate_candidate_ref(
            repo,
            operation_id="op-mirror-failure",
            binding="session",
            expected_head=expected,
        )
