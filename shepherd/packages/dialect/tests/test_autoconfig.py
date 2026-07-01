"""W4 re-pins — autoconfig's mechanical half (authoring re-pin plan, D3).

Same-intent re-pins of `runtime/unit/task/test_autoconfig.py`'s mechanical
rows: the ``Infer`` marker, ``extract_infer_fields``, ``build_inference_model``.
The class-form ``Input(infer=True)`` rows retire (tranche D1); the LLM-backed
inference rows ride the battery tranche (D3's fence).
"""

from typing import Annotated

from pydantic import BaseModel, Field

from shepherd_dialect.autoconfig import Infer, build_inference_model, extract_infer_fields


class ReviewConfig(BaseModel):
    guidelines: Annotated[str, Infer] = Field(default="", description="House review style")
    max_findings: Annotated[int, Infer] = Field(default=10, description="Cap")
    repo_name: str = "fixed"  # not inferable
    tags: Annotated[list[str], Infer] = Field(default_factory=list, description="Labels")


class TestExtractInferFields:
    def test_extracts_only_infer_marked_fields(self):
        fields = extract_infer_fields(ReviewConfig)
        assert set(fields) == {"guidelines", "max_findings", "tags"}

    def test_carries_type_description_default(self):
        info = extract_infer_fields(ReviewConfig)["max_findings"]
        assert info["type"] is int
        assert info["description"] == "Cap"
        assert info["default"] == 10

    def test_default_factory_flagged_and_materialized(self):
        info = extract_infer_fields(ReviewConfig)["tags"]
        assert info["has_default_factory"] is True
        assert info["default"] == []

    def test_infer_call_syntax_wraps_a_type(self):
        class C(BaseModel):
            note: Infer(str) = ""  # type: ignore[valid-type]

        assert set(extract_infer_fields(C)) == {"note"}

    def test_no_infer_fields_means_empty(self):
        class Plain(BaseModel):
            x: int = 1

        assert extract_infer_fields(Plain) == {}


class TestBuildInferenceModel:
    def test_model_name_and_field_subset(self):
        model = build_inference_model(ReviewConfig)
        assert model.__name__ == "InferReviewConfig"
        assert set(model.model_fields) == {"guidelines", "max_findings", "tags"}

    def test_defaults_and_descriptions_survive(self):
        model = build_inference_model(ReviewConfig)
        instance = model()
        assert instance.max_findings == 10
        assert instance.tags == []
        assert model.model_fields["guidelines"].description == "House review style"

    def test_built_model_validates(self):
        model = build_inference_model(ReviewConfig)
        filled = model.model_validate({"guidelines": "be kind", "max_findings": 3, "tags": ["a"]})
        assert filled.guidelines == "be kind"
