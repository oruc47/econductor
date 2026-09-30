# Validation status

## Local smoke validation (2026-09-30)

On macOS 26.5.2 with an Apple M5 Max and 36 GB unified memory, `econductor doctor` applied the version 3 default-deny sandbox and launched both the data and Metal workers. Its synthetic probe confirmed approved project reads and artifact writes succeed while reads and writes outside the selected project and a loopback network connection are blocked. A small MLX array calculation completed on the GPU within the inference profile.

Model-free smoke runs also completed the built-in file listing tool and Python, R, and licensed Stata scripts in temporary projects. The Python analysis imported NumPy and DuckDB from the pinned environment. Stata's startup/license banner is retained in its local log artifact but omitted from the agent's concise result. Ruff and Python compilation checks passed. No model weights were downloaded or evaluated, so real-model generation quality, memory use, and speed remain unmeasured.

The policy uses Apple's private `system.sb` base rules, which may change with macOS. Repeat `doctor` and runtime checks on each OS version used by colleagues. This smoke check is not an institutional security certification.

The command `econductor evaluate <preset>` is intended to run a reproducible synthetic task suite against a previously downloaded model. It refuses to download weights. Its report records preset/revision, tool completion, answer checks, latency, generation tokens, and peak MLX memory. Evaluation results are local artifacts; they are not shipped as model quality claims.

## Release acceptance

- On every supported Mac, setup and `doctor` confirm Metal and macOS sandbox behavior.
- Network attempts fail in inference, built-in tools, and Python, R, and Stata child processes; explicit downloads succeed only in the download helper.
- Credential files and session transcripts remain unreadable to workers, external symlinks are rejected, and source datasets cannot be overwritten.
- Each listed preset loads from a local path; the model evaluation report is generated only after the researcher explicitly downloads that preset.
- Synthetic SQL results match known values; Python/R/Stata scripts save logs and outputs to isolated artifacts; PDF/Markdown citations point to source pages/files.
- A separately invoked large-workload run queries a synthetic 20 GB Parquet dataset under the configured DuckDB memory cap.
