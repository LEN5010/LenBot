"""Literal lookup of the implemented, permitted low-frequency tool pool."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


DISCOVERY_REQUIRED_NAMES = frozenset({"scene_control", "schedule_list", "schedule_cancel", "persona_knowledge", "open_forward", "member_info", "transcribe"})


DEFERRED_NAMES = frozenset({"schedule", "memory", "delegate", "task", "send_file", "web_search", "web_read", "scene_control", "schedule_list", "schedule_cancel", "persona_knowledge", "open_forward", "member_info", "transcribe"})


def model_schema(schema: dict | bool) -> dict | bool:
    """Remove generated titles from owned schema nodes, not business data/maps."""
    if isinstance(schema, bool):
        return schema
    result = {}
    for key, value in schema.items():
        if key == "title":
            continue
        if key in {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}:
            value = {name: model_schema(child) for name, child in value.items()}
        elif key in {"allOf", "anyOf", "oneOf", "prefixItems"}:
            value = [model_schema(child) for child in value]
        elif key in {"items", "additionalProperties", "unevaluatedProperties", "unevaluatedItems",
                     "contains", "propertyNames", "not", "if", "then", "else"}:
            value = model_schema(value)
        result[key] = value
    return result


class ToolSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


TOOL_SEARCH = {"type": "function", "function": {
    "name": "tool_search", "description": "按名称或说明中的原词查找当前已允许的低频工具，最多返回 5 项。"
    "命中工具从下一次模型请求开始可用；参数错误不会改变已加载工具。",
    "parameters": ToolSearchArguments.model_json_schema(),
}}


def search_tools(query: str, pool: list[dict]) -> list[dict]:
    query = query.casefold()
    matches = []
    for tool in pool:
        function = tool["function"]
        name = function["name"].casefold()
        if name == query:
            rank = 0
        elif name.startswith(query):
            rank = 1
        elif query in name or query in function["description"].casefold():
            rank = 2
        else:
            continue
        matches.append((rank, name, tool))
    matches.sort(key=lambda item: (item[0], item[1]))
    return [tool for _, _, tool in matches[:5]]
