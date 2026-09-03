"""Built-in plugin registry (ADR-0021).

Discovery = this explicit registry: V2 ships exactly two real plugins
(one Sensor + one Tool). Configuration and enable flags persist in the
`plugins_state` dynamic config; loading happens at AgentRuntime.start().
"""

from len_bot.plugins.base import BasePlugin
from len_bot.plugins.builtin.bilibili_live import BilibiliLiveSensor
from len_bot.plugins.builtin.web_search import WebSearchToolPlugin

BUILTIN_PLUGINS: dict[str, type[BasePlugin]] = {
    "bilibili_live_sensor": BilibiliLiveSensor,
    "web_search_tool": WebSearchToolPlugin,
}
