"""Literal lookup of the implemented, permitted low-frequency tool pool."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


DEFERRED_NAMES = frozenset({"schedule_list", "schedule_cancel", "persona_knowledge"})


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
