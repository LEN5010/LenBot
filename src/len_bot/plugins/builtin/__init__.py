"""The explicit builtin catalog; importing schemas never loads plugin clients."""


BUILTIN_PLUGIN_INFO = {
    "bilibili_live_sensor": {
        "name":"哔哩哔哩直播监测", "description":"监测明确订阅成员的实际开播与下播事实。",
        "plugin_type":"sensory", "registered_tools":["get_live_status","get_live_subscriptions"],
        "emitted_events":["LIVE_STARTED", "LIVE_ENDED"],
    },
    "web_search_tool": {
        "name":"实时联网认知检索", "description":"对话与工作中的公开网页搜索、正文与PDF读取。",
        "plugin_type":"tool", "registered_tools":["web_search", "read_page"], "emitted_events":[],
    },
    "bilibili_content": {
        "name":"哔哩哔哩内容查询工具", "description":"对话与工作中的公开视频、搜索和用户动态查询。",
        "plugin_type":"tool", "registered_tools":["get_video_info", "search_bilibili", "get_dynamic_feed"], "emitted_events":[],
    },
    "asoul_calendar": {
        "name":"A-SOUL 直播日程", "description":"读取唯一ICS来源的真实日程，支持确定性日程命令。",
        "plugin_type":"tool", "registered_tools":["get_live_schedule"], "emitted_events":[],
    },
    "asoul_dynamics": {
        "name":"A-SOUL 动态与二创", "description":"读取指定动态站已抓取的动态、历史同日与二创。",
        "plugin_type":"tool", "registered_tools":["get_asoul_dynamics", "search_asoul_dynamics", "read_asoul_dynamic",
            "get_asoul_on_this_day", "search_asoul_fanart", "get_random_asoul_fanart"], "emitted_events":[],
    },
    "group_summary": {
        "name":"当前群按需总结", "description":"固定本群范围与快照，复用原工作运行器总结已保存人类消息。",
        "plugin_type":"tool", "registered_tools":["summarize_group_chat", "read_group_chat_window"], "emitted_events":[],
    },
}


def get_builtin_plugins():
    from .bilibili_live.plugin import BilibiliLiveSensor
    from .web_search.plugin import WebSearchToolPlugin
    from .bilibili_content.plugin import BilibiliContentPlugin
    from .asoul_calendar.plugin import AsoulCalendarPlugin
    from .asoul_dynamics.plugin import AsoulDynamicsPlugin
    from .group_summary.plugin import GroupSummaryPlugin

    return {
        "bilibili_live_sensor":BilibiliLiveSensor,
        "web_search_tool":WebSearchToolPlugin,
        "bilibili_content":BilibiliContentPlugin,
        "asoul_calendar":AsoulCalendarPlugin,
        "asoul_dynamics":AsoulDynamicsPlugin,
        "group_summary":GroupSummaryPlugin,
    }
