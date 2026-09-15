from pydantic import BaseModel, ConfigDict, Field
from len_bot.browser.worker import BrowserConfig


class BrowserPluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    browser: BrowserConfig = Field(title="浏览器参数",
        description="当前浏览器是同进程能力，白名单不是独立隔离；填写参数只决定实际访问范围")
