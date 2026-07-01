from shepherd_kernel_v3_reference.paths import source_path_ref, unhandled_source_path_ref


def test_source_path_ref_formats_selected_source_paths() -> None:
    assert source_path_ref("selection:1", "resumption:2", "branch:root") == (
        "path:selection:1/resumption:2/branch:root"
    )


def test_unhandled_source_path_ref_formats_top_level_source_paths() -> None:
    assert unhandled_source_path_ref("declaration:0", "branch:root") == ("path:unhandled/declaration:0/branch:root")
