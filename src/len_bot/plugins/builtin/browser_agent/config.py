from pydantic import BaseModel, ConfigDict
from len_bot.browser.worker import BrowserConfig


class BrowserPluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    browser: BrowserConfig
