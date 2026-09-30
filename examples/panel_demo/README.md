# Synthetic panel data demo

This folder contains a tiny, fictional panel dataset. It has no real people or institute data. In Econductor, start from this repository's root and try:

> Explore `examples/panel_demo/panel.csv`. Inspect its panel structure, summarize earnings by training status, estimate the relationship between training and earnings with unit and year controls, and save a coefficient plot and the reproducible script.

The exercise is deliberately small enough to run interactively. For your research, point Econductor at a separate project with your own data and scripts. Large datasets should start with SQL summaries and selected extracts.

`analysis.py` shows how to write a straightforward reproducible analysis: it reads from `PROJECT_ROOT` and saves all generated results under the isolated `OUTPUT_DIR` supplied by the Econductor runner.
