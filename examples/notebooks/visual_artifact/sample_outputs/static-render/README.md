# Static Render Outputs

Rendered outputs from running the three launch use-case notebooks and their internals
companions in static mode.

The current artifact is the gradient-descent infographic tile:

- UC1 compares `contour-map` with the planted `uphill-path` failure.
- UC2 right-sizes the tile QA evaluator and selects `mid`.
- UC3 recovers from a wrong-direction draft and selects the corrected retry.

Regenerate notebook HTML from the repository root:

```bash
cd examples/notebooks/visual_artifact/notebooks
uv run --group notebook jupyter nbconvert --to html --execute \
  visual_variant_studio.ipynb model_right_sizing_lab.ipynb visual_pipeline_recovery.ipynb \
  visual_variant_studio_internals.ipynb model_right_sizing_internals.ipynb visual_pipeline_recovery_internals.ipynb \
  --output-dir ../sample_outputs/static-render
```

Refresh the notebook PNG previews and PDFs from the generated HTML with Playwright:

```bash
cd examples/notebooks/visual_artifact
uv run --with playwright python sample_outputs/static-render/scripts/nb_shot.py \
  "$PWD/sample_outputs/static-render/visual_variant_studio.html" \
  "$PWD/sample_outputs/static-render/model_right_sizing_lab.html" \
  "$PWD/sample_outputs/static-render/visual_pipeline_recovery.html" \
  "$PWD/sample_outputs/static-render/visual_variant_studio_internals.html" \
  "$PWD/sample_outputs/static-render/model_right_sizing_internals.html" \
  "$PWD/sample_outputs/static-render/visual_pipeline_recovery_internals.html"
```

Refresh the helper PNGs from the current static tile renderer:

```bash
cd examples/notebooks/visual_artifact
uv run --with playwright --with pillow python sample_outputs/static-render/scripts/render_artifacts.py
uv run --with playwright --with pillow python sample_outputs/static-render/scripts/render_uc3.py
```
