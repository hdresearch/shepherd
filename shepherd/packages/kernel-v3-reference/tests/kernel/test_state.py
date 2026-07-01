from shepherd_kernel_v3_reference.kernel.state import MachineState
from shepherd_kernel_v3_reference.profiles import CORE_A


def test_machine_state_carries_profile_branch_and_trace_ids() -> None:
    state = MachineState()

    assert state.profile == CORE_A
    assert state.branch_ref == "branch:root"
    assert state.fresh_ref("event") == "event:0"
    assert state.fresh_ref("event") == "event:1"


def test_machine_state_tracks_terminal_and_consumed_source_paths() -> None:
    state = MachineState()

    assert state.mark_terminal_path("path:selection/source/branch:root") is True
    assert state.mark_terminal_path("path:selection/source/branch:root") is False

    assert state.consume_source_path("path:selection/source/branch:root") is True
    assert state.consume_source_path("path:selection/source/branch:root") is False
