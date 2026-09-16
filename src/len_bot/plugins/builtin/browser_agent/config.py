from pydantic import BaseModel, ConfigDict, Field
from len_bot.browser.worker import BrowserConfig


class BrowserPluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    browser: BrowserConfig = Field(title="浏览器参数",
        description="Gateway 模式使用独立浏览器容器；旧 worker 配置只保留显式试运行入口")
    image_ref: str = Field(default='browser', pattern=r'^[a-z][a-z0-9_-]{0,63}$',
        title='浏览器镜像引用', description='Gateway 已登记的 browser 类型镜像引用')
    network_policy: str = Field(default='public', pattern=r'^[a-z][a-z0-9_-]{0,63}$',
        title='浏览器网络策略', description='Gateway 已核验的公共出口代理策略引用')
