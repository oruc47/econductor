from __future__ import annotations

from econductor.types import ToolCall


def definition(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


STRING = {"type": "string"}
TOOLS = [
    definition(
        "list_files",
        "List up to 500 visible files inside the selected project. Omit path to list the project root; optional path must be inside that project.",
        {"path": STRING},
        [],
    ),
    definition(
        "read_file",
        "Read a bounded code/text file. Use dataset/document tools for binary files.",
        {"path": STRING},
        ["path"],
    ),
    definition(
        "search_files",
        "Search code/text for a literal phrase, returning file and line references.",
        {"query": STRING},
        ["query"],
    ),
    definition(
        "inspect_dataset",
        "Inspect CSV/TSV/Parquet/Stata/Excel schema, labels, and eight sample rows; never ingest full data into context.",
        {"path": STRING},
        ["path"],
    ),
    definition(
        "query",
        "Run one SELECT/WITH SQL query. Map table aliases to project dataset paths. Full results save to Parquet, preview at most 50 rows.",
        {"tables": {"type": "object", "additionalProperties": STRING}, "sql": STRING},
        ["tables", "sql"],
    ),
    definition(
        "search_documents",
        "Search local text PDFs, Markdown and text codebooks. Cite returned file/page references. Scanned PDF OCR unsupported.",
        {"query": STRING},
        ["query"],
    ),
    definition(
        "edit_file",
        "Propose complete content for a code/text file. Existing files require overwrite approval; source datasets cannot be modified.",
        {"path": STRING, "content": STRING},
        ["path", "content"],
    ),
    definition(
        "execute",
        "Execute an offline script. Working directory is its output folder, not the project. Read project files using environment PROJECT_ROOT; write results into OUTPUT_DIR/current directory. Python/R/Stata packages must already be installed. Save code and logs automatically.",
        {"language": {"type": "string", "enum": ["python", "r", "stata"]}, "code": STRING},
        ["language", "code"],
    ),
]
SCHEMAS = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS}
MUTATING = {"edit_file", "execute", "query"}
HEAVY = {"execute", "query", "inspect_dataset", "search_documents"}


def validate(call: ToolCall) -> None:
    if call.name not in SCHEMAS:
        raise ValueError(f"Unknown tool: {call.name}")
    schema = SCHEMAS[call.name]
    if not isinstance(call.arguments, dict):
        raise ValueError("Tool arguments must be an object.")
    if set(call.arguments) - schema["properties"].keys():
        raise ValueError("Unexpected tool arguments")
    if any(key not in call.arguments for key in schema["required"]):
        raise ValueError("Missing required tool arguments")
    for key, value in call.arguments.items():
        spec = schema["properties"][key]
        if spec["type"] == "string" and (not isinstance(value, str) or len(value) > 200000):
            raise ValueError(f"{key} must be a bounded string")
        if spec["type"] == "object" and (
            not isinstance(value, dict)
            or len(value) > 10
            or any(
                not isinstance(k, str) or not isinstance(v, str) or len(v) > 4096
                for k, v in value.items()
            )
        ):
            raise ValueError(f"{key} must map up to ten names to paths")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"Invalid {key}")
