# Extending Econductor

## Add an offline tool

Tools have a JSON schema in `econductor.tools.TOOLS`. Add its schema and validator handling there, dispatch it in `econductor.tool_worker`, and add a concise instruction to `econductor.agent.SYSTEM`. Workers receive one bounded JSON request over standard input and return one JSON result. Use `PathGuard` for every project path. Return compact previews and save large results as artifacts. Never put raw data in logs or error messages.

The worker gets the selected project as a read root and its own run directory as a write root, runs with an allowlisted environment, and has network denied by the macOS sandbox. Extending its system calls or writable paths changes the privacy boundary. Update `doctor`'s sandbox checks and review the generated profile when changing that boundary.

## Add a model provider

Implement the `Inference` protocol (`count`, `complete`, `unload`) in `econductor.types` and integrate it through `econductor.inference`. The provider must load local paths only and work under network denial. Model downloads belong in an explicit helper with no project/prompt input. Update `PRESETS`, validation, registry, and the model switch UI. Do not silently fall back to a remote service.

## Add a data format

Implement bounded conversion in `econductor.data.dataset_relation`, with a deterministic cache key based on source identity and metadata. Preserve labels and missing-value policy, write through a temporary cache directory, then atomically mark the cache complete. Do not mutate source data. Return schema plus a small sample, not full records.

## Share with the institute

Point colleagues to this repository and its pinned lock file. Ask the institute security office to review sandbox profiles, model licenses, retention rules, and supported runtimes before using restricted or regulated datasets. V1 provides local tools, not institutional approval or certification.
