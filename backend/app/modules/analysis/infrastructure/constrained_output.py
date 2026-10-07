"""Constrain generated references to exact frozen evidence, not model guesses."""
from pydantic import BaseModel


def frozen_output_schema(model: type[BaseModel], aliases: list[str]) -> dict[str, object]:
    schema = model.model_json_schema()
    definitions = schema.get("$defs", {})
    for name, field in (("NativeSourceFinding", "document_url"),
                        ("ReportSource", "url"), ("SourceArticle", "url")):
        if name in definitions:
            if name in {"ReportSource", "SourceArticle"}:
                # The exact frozen enum already constrains these valid URL
                # aliases. Some compatible decoders fail on format=uri combined
                # with enum, returning HTTP 200 with zero tokens/finish=length.
                # Do not relax the allowlist: local AnyHttpUrl validation and
                # manifest membership validation remain mandatory as before.
                definitions[name]["properties"][field] = {"type": "string", "enum": list(aliases)}
            else:
                definitions[name]["properties"][field]["enum"] = list(aliases)
    if "evidence_map" in schema.get("properties", {}):
        schema["properties"]["evidence_map"] = {
            "type": "object", "properties": {alias: {"type": "string"} for alias in aliases},
        }

    def strict(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for child in node.values():
                strict(child)
        elif isinstance(node, list):
            for child in node:
                strict(child)

    strict(schema)
    return schema
