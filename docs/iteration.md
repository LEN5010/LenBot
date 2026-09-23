# 当前任务

更新于 2026-09-23。任务状态只见[产品路线](plan/README.md)，稳定合同见[文档入口](README.md)，此前过程见[历史入口](history/README.md)。

## 当前批次

- 分支 `feat/s0-product-contract`，基线 `7e8a07c`，开始时工作区干净；上一批锁定 workspace worker 的直接／间接 Python 包，并在本机完成主镜像与 worker 的 arm64、交叉 amd64 构建，未运行服务或群业务。
- S0-03／动态简图资源边界：原 `asoul_dynamics` 在富卡失败后直接找相邻 `asoul_calendar/resources/font.ttf`，找不到再找自身并不存在的 `resources/font.ttf`。现有 `len_bot.cards.BUNDLED_CARD_FONT` 已为直播卡片提供同一随包文件的公共定位；本批只复用它，不移动或复制字体、不新增配置和兼容路径。

## 本批交付与核对

`AsoulDynamicsPlugin._render_source_card` 的简图分支改用 `BUNDLED_CARD_FONT`。文件缺失时在此边界明确报“动态卡片字体文件不存在”，不扫描机器字体或改用其他资源。富卡、来源查询、图片登记与发送链均不变；当前公共常量仍指向内置日历目录下同一原件，路径抽离不等于字体授权或打包边界已决定。

同步[插件开发](plugins.md#可选卡片公共入口)、[插件兼容清单](plugin-compatibility.md)和路线 S0-03 的当前证据；基线[依赖测绘](plan/s0-03-dependency-boundary.md)仍按旧提交保留，不把当时的静态发现涂改成当前事实。群报告仍要求运营明确 `render_font_path`；代码和素材许可待维护者决定，本批不动样例配置或真实文件。

- `uv --cache-dir /private/tmp/lenbot-uv-cache run --no-sync python -m compileall -q src/len_bot/plugins/builtin/asoul_dynamics/plugin.py` 退出 0；`git diff --check` 退出 0。仅证语法与差异格式，未获准同版页面、插件装载或真实卡片现场。
- 未新增、修改或运行测试、夹具、断言式探针、自动截图、回放、故障注入、压力或覆盖率任务；未读取真实配置／业务库、启动服务、调用模型／平台或实发。没有本批实际业务失败原文。

## 待决定与接续

1. S0-03 其他已盘点耦合和群报告字体明确路径／素材授权仍未收口；本批只移除动态简图的一条相邻插件路径，不把 S0-03 标为已验收。
2. S5-01 双架构本机构建已完成；本机 SnowLuma 以协议而非固定发行版本名单判读。目标机器正常安装、模型职责、报告／文件真实回执及 S5-06 公开承诺仍待授权和证据。
3. S2 仍仅 `source_window_only`，原生交换及必要回复片段保存边界待维护者答复；数据分类期限、许可证／素材授权、S6 候选和 S7 外部闭环仍待决定或现场记录。
4. 仅阶段性本地提交，不推送、合并、部署或实发，总体目标继续。
