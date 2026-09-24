# S0-04 / S0-06 事实底稿：开源授权清点与验收边界

> 基线资料：以下事实与源码行号限定于文中标明的核对时点；任务当前状态只见[路线入口](README.md)，后续实施见[当前任务](../iteration.md)。草案未确认部分不视为已采用。

2026-09-24 后续核对：本地 wheel 与 sdist→wheel 均已实际构建，wheel 内含原日历字体（25,631,744 字节）及 B 站卡片 logo（53,782 字节），详见[当日打包记录](../history/iteration-20260924-s6.md)。`pyproject.toml` 的简述占位已去除；当前 sdist 保留前端源码与锁文件、只排除本机 `node_modules`，wheel 仍只带已构建面板。这些是**基线之后的源码和产物事实**，不能把下表原行号与“仍是占位”“只排除整个 frontend”等旧描述当作当前状态；代码／素材许可与实际分发仍待维护者决定。

- **核对 commit**：任务下发时 `ad41a5a71a4b5ec3693f4cc846342964c5b0b8c6`（`feat/s0-product-contract`）；核对过程中工作区被其他批次推进到 `f5279f88b80f31734af4fad77c7c96c4a0c5e107`。`git diff --name-only ad41a5a..f5279f8` 只含 `docs/plan/*` 与 `.github/ISSUE_TEMPLATE/*`、`.github/PULL_REQUEST_TEMPLATE.md`，**没有任何业务源码、配置、依赖或素材变化**，因此下列源码结论对两个 commit 同样成立。
- **核对时间**：2026-09-21 23:42 (+0900)。
- **只读方式**：仅 `git` / `grep` / `glob` / `read` / `ls`。未改业务文件，未 commit/checkout/stash，未跑测试/夹具/探针/截图，未启服务，未调用模型或平台，未读 `len_bot.db`，未联网。唯一写操作是本文件。
- **一句话结论**：仓库根目录**没有 LICENSE/COPYING/NOTICE，也没有任何许可证声明或 SPDX 标记**；`pyproject.toml` 无 `license` 字段（构建出的 METADATA 亦无）；代码侧存在一处 **AGPL-3.0 的 vendored 移植**（`asoul_calendar`）与一处 MIT 的上游参考（`group_summary`）；素材与人设文本大量入库但**来源多为“用户提供／运营提供”，没有一条记录了再分发许可**；验收侧当前只允许「阅读、编译构建、获准环境正常启动与人工操作」，CI 只做 compileall 与前端 build，**没有任何测试入口**。

---

## A 部分｜S0-04 代码与素材的开源授权清点

### A.1 许可证文件与声明清点

| 项 | 位置 | 现状 | 未确认 |
|---|---|---|---|
| 根 LICENSE / COPYING / NOTICE | 仓库根 | **不存在**。`git log --all --diff-filter=A -- 'LICENSE*' 'COPYING*' 'NOTICE*'` 全历史零命中 | 维护者是否有意暂缓 |
| `pyproject.toml` 许可字段 | `pyproject.toml:1-51` | **无 `license`、无 `license-files`、无 classifiers**。`description` 仍是占位 `"Add your description here"`（`:4`） | 会选择哪个许可证 |
| 构建元数据 license | `.venv/.../len_bot-0.1.0.dist-info/METADATA` | 只有 `Author: LEN5010 <1649211052@qq.com>`，**无 `License:` / `License-Expression:` 字段** | — |
| README 许可声明 | `README.md` | 全文无“许可 / license / 开源”章节；只提到人格素材来源见 `docs/persona/diana/README.md`（`:56`） | 是否要加许可节 |
| 源码文件头 SPDX / 版权头 | `src/**` | **零命中**。`git grep -n SPDX` 无结果；`git grep -niE 'copyright｜©' src` 的命中全部落在 AGPL 许可证正文内部（`asoul_calendar/LICENSE`） | — |
| vendored 插件许可证原件 | `src/len_bot/plugins/builtin/asoul_calendar/LICENSE` | 存在，正文是 **GNU AGPL v3**（`LICENSE:1-2`） | 该插件与本仓其余代码同包分发时的义务范围 |
| vendored 上游许可证原件 | `src/len_bot/plugins/builtin/group_summary/LICENSE.upstream` | 存在，**MIT**，`Copyright (c) 2025 Helian Nuits`（`:1-3`） | — |
| 来源说明文件 | `src/len_bot/plugins/builtin/{asoul_calendar,asoul_dynamics,group_summary,gscore_adapter}/SOURCE.md` | 四份，逐条记录上游仓库与参考版本 | 见 A.4 |
| 社区健康/治理文件 | 仓库根 | **无** CHANGELOG / SECURITY / CONTRIBUTING / CODE_OF_CONDUCT / AUTHORS | 保留政策与支持范围（见决策清单） |
| 贡献模板中的许可条款 | `.github/ISSUE_TEMPLATE/*.md`、`.github/PULL_REQUEST_TEMPLATE.md` | 存在，但**全文无许可、无 CLA/DCO、无 inbound=outbound 声明** | 外部贡献的授权声明 |
| 实际分发形态 | `pyproject.toml:34-45` | `uv_build` 打成单一 wheel；AGPL 插件目录被 `source-exclude`/`wheel-exclude` 只排除了前端，**插件目录仍在包内** | — |

### A.2 直接依赖的声明许可

**读法说明（重要）**：`uv.lock` **完全不记录 license**——`grep -c license uv.lock` 返回 `0`。因此下表“声明许可”一列的实际读取位置是**本机 `.venv` 已安装包的 `dist-info/METADATA`**，其版本与 `uv.lock` 锁定版本逐一对应。`.venv` 与 `node_modules` 都不在 Git 内，**许可证原文并不随仓库分发**；这是本表最大的读取边界，已在“未确认与读不到”单列。

| 依赖 | 声明许可 | 来源 | 风险提示（事实陈述，非法律结论） |
|---|---|---|---|
| aiosqlite 0.22.1 | MIT（仅 classifier `License :: OSI Approved :: MIT License`，无 `License:` 字段） | pyproject.toml:11；METADATA | — |
| openai 3.7.0 | Apache-2.0 | pyproject.toml:12；METADATA `License-Expression` | — |
| pydantic 2.13.5 | MIT | pyproject.toml:13；METADATA | — |
| websockets 17.1 | BSD-3-Clause | pyproject.toml:14；METADATA | — |
| fastapi 0.141.1 | MIT | pyproject.toml:15；METADATA | — |
| uvicorn 0.52.4 | BSD-3-Clause | pyproject.toml:16；METADATA | — |
| python-multipart 0.0.32 | Apache-2.0 | pyproject.toml:17；METADATA | — |
| httpx 0.28.1 | BSD-3-Clause | pyproject.toml:18；METADATA | — |
| trafilatura 2.2.0 | Apache-2.0 | pyproject.toml:19；METADATA | 传递依赖 `tld` 为三选一许可，见下 |
| pillow 12.3.0 | MIT-CMU | pyproject.toml:20；METADATA | — |
| pypdfium2 5.13.0 | `BSD-3-Clause, Apache-2.0, dependency licenses` | pyproject.toml:21；METADATA | **捆绑二进制**：`License-File` 列出 PDFium、freetype、libjpeg-turbo、libpng、libtiff、ICU、lcms、abseil 等一大堆第三方许可，且**每个平台目录各一套**（`data/darwin_arm64/BUILD_LICENSES/*`） |
| jinja2 3.1.6 | BSD（仅 classifier；无 `License:` 字段） | pyproject.toml:22；METADATA | — |
| qrcode 8.2 | BSD（classifier `License :: OSI Approved :: BSD License`） | pyproject.toml:23；METADATA | — |
| playwright 1.62.0（`[browser]` 可选） | Apache-2.0 | pyproject.toml:30-32；METADATA | 镜像/环境中另会下载 **Chromium 二进制**（`containers/browser/Dockerfile:9`），其许可是另一套 |
| **tld 0.13.2（传递）** | `MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later` | METADATA | **三选一**，是当前依赖树里唯一带 GPL/LGPL 分支的条目 |
| **certifi 2026.7.22（传递）** | MPL-2.0 | METADATA | 文件级 copyleft |
| regex 2026.9.3（传递） | `Apache-2.0 AND CNRI-Python` | METADATA | — |
| python-dateutil 2.9.0.post0（传递） | `Dual License`（classifier BSD） | METADATA | 声明不规范，需补读原文 |
| greenlet 3.5.5（传递） | `MIT AND PSF-2.0` | METADATA | — |
| 前端直接依赖 | vue / vue-router / vuetify / vite / @vitejs/plugin-vue / vite-plugin-vuetify = **MIT**；@mdi/js = **Apache-2.0** | `src/len_bot/web/frontend/package.json` + `node_modules/*/package.json` | — |
| 前端传递依赖汇总 | `package-lock.json` 内 93 处 `license`：MIT ×89、Apache-2.0 ×1、BSD-3-Clause ×1、BSD-2-Clause ×1、ISC ×1 | `package-lock.json` | 无 copyleft 命中 |
| 容器配方引入的软件 | numpy / pandas / matplotlib / pillow（`containers/workspace/Dockerfile:7`）、ffmpeg + ca-certificates（`containers/media/Dockerfile:2-3`）、fonts-noto-cjk（workspace/browser）、chromium（browser） | 各 Dockerfile | 均为构建期安装，非 vendored；许可随镜像分发，仓库内无清单 |

### A.3 角色素材清点

**分三类**：①随源码分发（在 Git 内）②仅本机运行素材（`.gitignore` 排除）③仅文档资料（明确声明“不是运行时目录”）。

| 素材 | 路径 | 是否入库 | 来源可考性 | 建议声明 |
|---|---|---|---|---|
| 人格原文（6 份 md） | `docs/persona/diana/sources/*.md` | **入库** | **来源可考**：`docs/persona/diana/README.md:5` 记“用户于 2026-09-06 提供，来自桌面 ASOUL 目录下的两份知识文档与四份嘉然梗卡”；但**未记录再分发许可** | 需单列：用户提供文本的授权范围 |
| 人格图文索引 | `docs/persona/diana/sticker-pack-20260906.json` | **入库** | **来源可考**：`diana/README.md:22` 记“用户于 2026-09-06 从本地 `Diana/表情包` 目录提供 49 张有效图片（25 GIF / 24 静态，约 45.85MB）”；JSON 内 `source` 字段仅写“运营提供的本地表情包目录” | 需单列：原图授权；索引本身可随文档声明 |
| 人设原文（内嵌源码） | `src/len_bot/cognition/diana.py`（`PRESET_ID = "diana-v4"`，含 `character_context` 大段角色文本） | **入库** | **来源可考**（文件头注明 reference sources: `docs/persona/diana`），但**角色文本随代码一起打包进 wheel** | 需单列：角色文本是否按代码许可还是按素材许可 |
| A-SOUL 资料审阅稿 | `docs/persona/asoul/character-context-20260921.md` | **入库** | **来源可考**：`asoul/README.md` 记依据用户确认口径 + 桌面 `ASOUL/知识` 六份原稿，整理日 2026-09-21 | 需单列：资料稿与用户口径的授权 |
| 五张常服参考图 | `docs/persona/asoul/reference-images/20260921/*.png`（5 个，约 1.33MB） | **入库** | **来源可考**：同目录 `README.md:3` 记“由用户在本次设计讨论中直接提供……原样副本，没有裁剪、重绘或生成衍生图” | 需单列：用户供图的再分发许可 |
| 表情审阅表 | `docs/persona/asoul/sticker-review-20260921.md` | **入库** | **来源可考**：`:3` 记来源 `/Users/len5010/file/Diana/表情包1`，19 个原文件 | 表可随文档；原图另列 |
| 枝江三视图集（外部站点） | 仅链接：`reference-images/20260921/README.md:15` → `https://asoul-gallery.pages.dev/` | **未入库** | **来源可考但条件明确**：该 README 原样记录“素材含录播截图与生成式补绘，提供免费非商用使用” | **需单列**：非商用条款与项目传播目标是否相容，由维护者判断 |
| 日程成员表情（运行目录） | `media/schedule-avatars/{贝拉,嘉然,乃琳,思诺,心宜}/`（合计 313 文件，约 196MB） | **未入库**（`.gitignore` `/media/`） | **来源未记录**：全仓 `grep schedule-avatars` 在 `src/`、`docs/` **零命中**，唯一引用是本机未入库的 `lenbot.config.json:1226-1230`；文件名形如 `[嘉然_biu]-0_sticker_static.png` | 需单列：成员表情包的来源与授权 |
| 聊天媒体（运行目录） | `media/` 顶层 2800 个文件（整体 2.0GB） | **未入库** | **来源未记录**：运行期从群消息落盘，无来源台账 | 一般不随发行；需说明运行数据不进发行包 |
| 日程卡片字体 | `src/len_bot/plugins/builtin/asoul_calendar/resources/font.ttf`（25,631,744 B） | **入库** | **来源可考但授权明确缺失**：`asoul_calendar/SOURCE.md:7` 原文“保留上游同名字体原件；上游在本版本未提供独立字体许可证文件，**本地不另行声明其授权范围**” | **必须单列**：字体授权未知，且它已被 `bilibili_live`、`asoul_dynamics`、`group_summary` 三处复用（`bilibili_live/plugin.py:194`、`lenbot.config.example.json:171`） |
| B 站品牌 logo | `src/len_bot/cards/bilibili/logo.png`（53,782 B） | **入库** | **来源未记录**：`cards/bilibili/context.py:29` 只写常量路径，无来源/授权注释 | **必须单列**：第三方平台商标标识 |
| 控制台标识 | `src/len_bot/web/frontend/src/assets/lenbot-mark.svg`（357 B） | **入库** | **来源未记录**（无来源注释）；从 SVG 内容看似为本项目自有标识 | 建议随代码许可，需维护者确认系自创 |
| 前端构建产物 | `src/len_bot/web/static/dist/` | **未入库** | 由本机/镜像 Node 阶段生成 | 不需要单独素材声明 |
| 运行时拉取的外部图片 | B 站头像/封面（`cards/bilibili/source.py`）、枝江三视图站 | **不落地到仓库** | 运行期取用，内联为 data URI | 需说明运行期第三方的使用边界 |

**运行时素材下载结论（事实）**：仓库内**没有**任何“启动时下载角色素材”的实现。人设文本在 `cognition/diana.py` 内；表情/头像必须由运营从本机目录登记为资产（`media/service.py:105` 的根目录 = `db_path` 同级的 `media/`）。外部网络来源只有日历 ICS（`asoul.love/calendar.ics`）、动态站（`len5010.top/dynamics`）与 B 站接口。

### A.4 vendored（直接复制/移植）的第三方代码

| 文件 | 来源线索 | 是否保留上游许可证 | 备注 |
|---|---|---|---|
| `src/len_bot/plugins/builtin/asoul_calendar/calendar.py` | 文件头 `:3-4`：“ICS folding, text escaping and date decoding are adapted from **LEN5010/astrbot_plugin_asoul at 5a945f6**” | **是**：同目录 `LICENSE`（AGPL-3.0） | `SOURCE.md` 给出完整参考版本 `5a945f695ecaa434ff71d402a344f8e9e40feab0` |
| `src/len_bot/plugins/builtin/asoul_calendar/render.py` | 文件头 `:1-5`：“adapted from the upstream Pillow layout … `astrbot_plugin_asoul/asoul_render.py`” | 同上 | — |
| `src/len_bot/plugins/builtin/asoul_calendar/resources/font.ttf` | `SOURCE.md:7`：保留上游同名字体原件 | **无独立字体许可文件** | 见 A.3 |
| `src/len_bot/cards/schedule/__init__.py` | 注释 `:16`：“Carried over from the **reference plugin's** schedule highlighting, recoloured…” | **未具名、未保留许可证** | “reference plugin” 从上下文推断为 `astrbot_plugin_asoul`，**但该文件没有点名** → 来源部分未确认 |
| `src/len_bot/cards/schedule/avatars.py` | 文件头 `:3`：“Ported from the **reference plugin's** `_select_avatar_paths`” | **未具名、未保留许可证** | 同上；函数名是强线索但非声明 |
| `src/len_bot/cards/theme.py` | 文件头 `:9-11`：“The reference plugin drew its schedule in a separate warm-beige scheme (#f3ebdf ground, #c56d49 kicker)” | 未具名 | 只保留配色数值，非大段代码 |
| `src/len_bot/plugins/builtin/group_summary/*.py`（8 文件） | `SOURCE.md:3`：统计口径与话题组织参考 `astrbot_plugin_qq_group_daily_analysis`（`ce320dc`）的 `statistics_calculator.py`、`single_daily_report_service.py` | **是**：`LICENSE.upstream`（MIT, Helian Nuits） | 文末亦记作者与许可证位置 |
| `src/len_bot/plugins/builtin/gscore_adapter/*` | `SOURCE.md:1-3` 对照 `KimigaiiWuyi/astrbot_plugin_gscore_adapter` 的 `models.py`/`client.py` | 无原件；`SOURCE.md:7` 明确“**未复制其 AstrBot 代码或运行时**” | 声明为协议对照，非 vendored |
| `src/len_bot/plugins/builtin/asoul_dynamics/*` | `SOURCE.md:1-3` 参考 `LEN5010/astrbot_plugin_dynamic_asoul@7ccd900`；`:9` 明确“上游未包含 LICENSE 文件；……**不为上游补写或假定许可证**。实现只使用已记录的接口协议” | 无 | 声明为接口参考，非 vendored |
| `src/len_bot/cognition/diana.py` | 文件头：`Operator-authored persona preset; reference sources: docs/persona/diana` | 无 | **operator-authored**，但内嵌角色文本 |

**需要指出的事实（不下法律结论）**：`asoul_calendar` 的移植按其自带原件是 **AGPL-3.0**，而当前打包方式（`pyproject.toml:34-45`）把它与其余全部代码打**同一个 wheel**，`.dockerignore` 也把 `src/` 整体纳入镜像构建上下文。这是“同一发行物内含 AGPL 组件”的事实，是否触发对外提供对应源码的义务由维护者判断。

### A.5 代码授权与素材授权必须分开声明的结论

**可以分开、也必须分开**，理由全部是仓库内可核事实：

1. **来源不同**：代码的第三方来源有 4 份 `SOURCE.md` + 2 份许可证原件可追（A.4）；素材的唯一来源记录是散文式“用户提供／运营提供”（A.3），且**没有任何一条记录了许可条款**。
2. **许可条件不同**：代码侧已出现 AGPL-3.0 与 MIT 两种明确条款；素材侧唯一写明条件的第三方（枝江三视图集）恰恰是“**免费非商用**”。
3. **侵权面不同**：代码侧的未决项是 copyleft 义务与打包方式；素材侧的未决项是**角色形象权、商标与用户供图授权**——`cards/bilibili/logo.png` 是平台商标，`font.ttf` 是授权不明的字体，人设文本源于粉丝社群梗。
4. **分发边界不同**：素材里的运行媒体（`media/`，2.0GB）本来就不入 Git、不入 wheel；若沿用代码许可会让使用者误以为这些素材也在授权范围内。

**需要维护者补充说明的具体条目清单**（不含结论）：

- [ ] 代码许可证选择（尚未决定）。
- [ ] `asoul_calendar`（AGPL-3.0 移植）与本包其余代码同 wheel 分发时的处理方式。
- [ ] `asoul_calendar/resources/font.ttf` 的字体授权——上游未提供，且被三个插件复用。
- [ ] `cards/bilibili/logo.png` 的使用依据。
- [ ] `user 提供` 类素材（6 份人设原文、49 张表情索引、5 张常服参考图、19 张待审表情）的再分发授权记录。
- [ ] `docs/persona/asoul/reference-images/20260921/README.md:15` 所述枝江三视图集的**非商用**条件与本项目传播目标的关系。
- [ ] `src/len_bot/cognition/diana.py` 内嵌角色文本适用的授权类别。
- [ ] `media/schedule-avatars/` 与 `media/` 运行素材是否随任何一种发行物分发。
- [ ] `cards/schedule/{__init__.py,avatars.py}`、`cards/theme.py` 中未具名 “reference plugin” 的确切来源与许可。
- [ ] 是否需要 NOTICE / 第三方许可汇总（当前依赖许可原文都不在仓库内）。
- [ ] 外部贡献的授权声明形式（当前 PR/Issue 模板无 CLA/DCO/inbound=outbound）。

---

## B 部分｜S0-06 验收边界与首个支持组合

### B.1 当前实际允许 / 禁止的验证手段（含原文位置）

| 类别 | 内容 | 原文位置 |
|---|---|---|
| **允许** | 阅读（源码与文档） | `AGENTS.md:21`「只做阅读、编译构建和已获准环境中的正常启动与人工操作」；`docs/LenBot_群聊体验与可靠执行_完整改造计划_20260920.md:330`「各条的"核对"指阅读、编译/构建或获准正常人工操作，不授权新增测试」 |
| **允许** | 编译 Python | `AGENTS.md:21`；`.github/workflows/ci.yml:27` `uv run --no-dev python -m compileall -q src/len_bot` |
| **允许** | 前端构建 | `AGENTS.md:21`「改前端时在 `src/len_bot/web/frontend` 执行 `npm run build` 并检查实际改动页面」；`README.md:12,16`；`docs/operations.md:340-343` |
| **允许** | 已获准环境中的**正常启动**与**人工操作** | `AGENTS.md:21`；`README.md:7`「取得当次启动授权再运行」；`docs/LenBot_分阶段任务卡_20260921.md:11`「后续实施只能使用获准的阅读、编译构建和正常人工操作」 |
| **允许** | 只读核对既有真实记录（含脱敏引用） | `docs/LenBot_成熟开源项目路线书_20260921.md:662`「只记录必要信息，脱敏后用于支持」；`:680`「已有合适真实记录可只读核对」 |
| **禁止** | 新增 / 修改 / 运行测试、夹具、断言式探针、自动截图、覆盖率任务 | `AGENTS.md:14`（原文逐字）；任务卡`:11` 追加“回放、故障注入和压力任务” |
| **禁止** | 改检查来制造通过结果 | `AGENTS.md:14` 尾句 |
| **禁止** | 故障注入 / 自动回放 / 压力 / 模型评分 / 探针 | `docs/LenBot_分阶段任务卡_20260921.md:199`（S0-06「明确不做」）；`docs/LenBot_成熟开源项目路线书_20260921.md:720` |
| **禁止** | 把静态检查写成运行通过 | `AGENTS.md:20`「不把静态检查写成运行通过」；`:16`「工具注册≠执行，生成文件≠已上传，返回本地路径≠平台取得文件」 |
| **禁止** | 在未授权时启动生产 / 推送 / 真实发送 | `AGENTS.md:23`「提交、推送、生产启动和真实发送分别服从用户当前授权」 |
| **保留的覆盖限制（须公开）** | 该禁令使特殊竞态与故障分支难以取得回归证据；必须公开区分源码核对与实测，不承诺覆盖 | `docs/LenBot_成熟开源项目路线书_20260921.md:568`（原文）；`docs/LenBot_分阶段任务卡_20260921.md:190`「保留当前不运行测试的约束及其覆盖限制」 |

**注意**：`pyproject.toml:47-51` 的 dev 组声明了 `pytest>=9.1.1`、`pytest-asyncio>=1.4.0`，`tests/` 下有 32 个文件入库；但 CI 使用 `uv sync --no-dev`（`ci.yml:24`），**CI 不运行任何测试**，且 `AGENTS.md:14` 禁止运行它们。这是“测试资产存在但不可运行”的事实状态。

### B.2 现有构建 / 复核入口与覆盖边界

| 入口 | 实际命令 | 产物 | 覆盖边界 |
|---|---|---|---|
| 依赖安装 | `uv sync` / `uv sync --locked --no-dev --no-editable` | `.venv` | 只证明依赖解析与安装；`README.md:10`、`operations.md:21`、`deploy/linux/Dockerfile` build 阶段 |
| Python 语法复核 | `uv run --no-dev python -m compileall -q src/len_bot` | `__pycache__/*.pyc` | **仅语法**；`ci.yml:27`。历史批次亦用 `uv --cache-dir … run --no-sync python -m compileall -q`（`docs/iteration.md:733`） |
| 前端构建 | `cd src/len_bot/web/frontend && npm ci && npm run build` | `src/len_bot/web/static/dist/` | **仅前端编译与打包**；产物不进 Git（`README.md:16`、`.gitignore` `/src/len_bot/web/static/dist/`）。历史记录显示模块数 461—481、1.4—2.5 秒（`docs/iteration.md:481,734,789,815`） |
| 本机启动 | `uv run len-bot` | 运行进程 | 需根目录 `lenbot.config.json` + 当次启动授权；`README.md:13`、`operations.md:59` |
| 迁移脚本 | `uv run python scripts/migrate_observation_config.py [--write]` | 配置改写 | 先只读报告、再写回；`operations.md:257-258` |
| Gateway 启动 | `uv run python -m len_bot.services.worker_gateway --config <path>` | 网关进程 | 需单独部署；`operations.md:129` |
| 容器镜像 | `docker build -f containers/{workspace,browser,media}/Dockerfile -t … .` | worker 镜像 | 配方存在，**构建与 Linux 运行证据尚待目标环境取得**（`containers/media/README.md:1`） |
| 发布镜像 | `docker build -f deploy/linux/Dockerfile`（compose） | `lenbot:release` | 三阶段：Node 构建前端 → pip 装 uv → `uv sync --locked --no-dev --no-editable` → 运行镜像；`deploy/linux/Dockerfile`、`deploy/linux/compose.yaml` |
| CI | `.github/workflows/ci.yml`（5 步） | GitHub Actions 结果 | **只覆盖 compileall + npm build**；触发分支 `master`/`main`（`:5,7`），**当前 `feat/s0-product-contract` 不触发** |
| 打包边界 | `pyproject.toml:43-45` | wheel | `source-exclude`/`wheel-exclude` 仅排除 `frontend`；插件目录在包内 |

**结论**：现有入口**没有任何一层覆盖运行时行为**。编译只证明语法，`npm run build` 只证明前端打包，CI 无测试步骤；业务正确性只能靠获准环境的人工观察。

### B.3 首个支持组合候选

依据：`README.md:29-39`「当前能力边界」、`lenbot.config.example.json`、`src/len_bot/adapters/`、各插件描述符。

**样例配置的实际默认状态（事实）**：
- `models.providers = []`，`routing.{conversation,work,maintenance} = null`，`retrieval.{embedding,rerank} = null`（`lenbot.config.example.json:99-110`）→ **样例不启用任何模型渠道**。可用渠道只需满足 `api_style: Literal["openai"]`（`src/len_bot/cognition/providers.py:74-80`），即任意 OpenAI 兼容端点。
- `delivery.shadow = true`（`:111-113`）→ 样例默认 Shadow，表达只记录候选、**不实际发送**。
- `scenes = {}`（`:121`）→ **群列表为空**，不显式启用并加入群不会有任何回复或发送。
- 插件默认：**只有 `web_search_tool` 为 `enabled: true`**；`asoul_calendar`、`asoul_dynamics`、`bilibili_live_sensor`、`link_parser`、`group_summary`、`local_clock`、`gscore_adapter`、`browser_agent`、`workspace`、`bilibili_content` 全部 `enabled: false`。
- 平台适配器：只有 **OneBot 11**（`src/len_bot/adapters/onebot.py`）；文件上传仅支持 `napcat: upload_group_file_data_file_id` 与 `snowluma: upload_group_file`（`src/len_bot/adapters/file_upload.py:8-11`），且 `deployment_verified` 默认 `False`。
- 工作后端：`workspace.config.worker` 为 `python:3.13-slim` 直调宿主 Docker，`gateway: null`（`:214-230`）。

| 候选 | 组成 | 依据 | 状态判定 |
|---|---|---|---|
| **C1｜普通群聊主链（最可能的首个组合）** | OneBot 11 群聊 + 一个 OpenAI 兼容 provider（conversation 路由）+ `web_search_tool` + 关闭 Shadow + 一个明确群 | `README.md:22-27`、`:33`；`adapters/onebot.py`；`config.example:129-136` | 源码已接线；**样例中无 provider、无群、Shadow 开启**，全部需运营补配置 |
| **C2｜确定性只读插件链** | `asoul_calendar`（只读外部 ICS + 本地 Pillow 渲染）或 `local_clock`（纯本地、不调模型） | `asoul_calendar/SOURCE.md`；`local_plugins/local_clock/README.md` | 源码完整；**样例停用**；`asoul_calendar` 依赖 `font.ttf`（授权未定，见 A.3） |
| **C3｜本地群报告** | `group_summary`（读固定范围原始事件 + 现有工作预算） | `group_summary/SOURCE.md` | 源码完整（8 文件，约 1168 行）；**样例停用**；依赖 `render_font_path` 指向同一 `font.ttf`（`:171`） |
| C4｜长工作 + 文件交付 | `workspace` + 文件上传 | `README.md:25`；`operations.md:391` | **未就绪**：worker 无网络、需宿主容器运行时；`onebot_file_upload.deployment_verified=false`；`docs/iteration.md:27,189` 记“生成、MD 登记、下载、平台上传均未实际执行” |
| C5｜浏览器 / 媒体 | `browser_agent` + `media_analysis` | `operations.md:13-19`；`containers/browser|media/README.md` | **未部署**：媒体“始终需要 Gateway”；浏览器镜像构建尚未成功（`docs/iteration.md` 早期记录的 chromium 下载中断）；`gateway.config.example.json` 中 `public.deployment_verified: false` |
| C6｜可选自主能力 | `interest_share` / 心跳 / 睡眠延期 / B 站账号动作 / `gscore_adapter` | `README.md:36-37`；`config.py:76` 心跳默认 `False` | **默认关闭 + 待验收**：「真实模型、部署及群回执仍待获准环境核对」 |

**默认关闭 / 未部署 / 待验收清单（不选入首版稳定范围）**：`bilibili_live_sensor`、`bilibili_content`（账号写入）、`link_parser`、`asoul_dynamics`、`gscore_adapter`（Core 桥接）、`workspace`、`browser_agent`、`media_analysis`、`interest_share`、心跳、睡眠延期、`network_python`、`onebot_file_upload`、`semantic_retrieval`（`config_store.py:143` 默认 `False`）。

### B.4 基线验收表模板

字段对齐路线书 `:643-660` 观察记录模板、`:1215-1233` 证据表模板与任务卡 `:139` 关闭字段。

| 任务/目标 | 候选 commit | 配置修订 | 已启用能力 | 实际操作 | 实际结果 | 证据身份（event/call/job/action/receipt） | 状态（已提交/已送达/失败/未知） | 用量 | 仅静态核对项 | 未确认范围 |
|---|---|---|---|---|---|---|---|---|---|---|
| Sx-xx / Axx；一句话用户结果 | `ad41a5a`（+ 构建标识） | 根配置版本/保存时间；实际群与模型绑定 | provider / adapter 版本 / 插件 / 开关 | 执行者 + 具体动作 + 时间 | 只写观察到的，不写“通过” | event_id / episode_id / call_id / job_id+revision / action_id / receipt | 四态 + 是否真实送达 | 实际报告 / 估算 / 未知；含范围 | 文件:行号 + 未验证限制 | 未覆盖分支、缺材料、缺授权 |

（本表为空模板，未填入任何实测数据。）

### B.5 只能源码核对 / 没有同版运行证据的范围

**只能源码核对（不应写为实测通过）**：

- 提交、发布、送达三态的区分逻辑（`docs/architecture.md:119-121`）与 `receipt_delivery_status` 判定。
- 并发 / 竞态分支：同 batch 前序失败与未知的后续拒绝、episode 租约竞争、Gateway `termination_unconfirmed` 复用阻断（`architecture.md:69,119`）。
- 上下文压缩、摘要恢复与窗口锯齿的装配细节（`docs/context.md`）。
- 失败与未知的处置路径（`architecture.md:407` 资产状态投影、`:425` 平台动作 unknown 占用）。
- 全部 `enabled: false` 的插件内部实现（workspace / browser / media / gscore / interest_share）。
- 文件的“生成 ≠ 已上传”链路（`media/files.py:169-171` 的阻断投影）。

**没有同版运行证据（截至本底稿）**：

- 前端与后端同版的实际页面（`docs/iteration.md` 多个小节反复记“已构建，尚未在同版真实服务查看”；任务卡 S0-06 验收亦要求“编译、构建、人工观察、真实送达分开”）。
- 真实模型调用、真实群收发、真实平台 `message_id` / `file_id`（`README.md:37`、`iteration.md:27,189`）。
- 停机备份 / 恢复 / 升级的现场执行（`deploy/linux/release-evidence.template.md` 全表为“待填写／未运行”）。
- 容器镜像在目标 Linux 上的构建与运行（`containers/media/README.md:1`「构建及 Linux 运行证据尚待目标环境取得」）。
- 缓存收益与费用口径（`docs/serious-issue.md`、`iteration.md:875`「不能据命中率宣布一次观察只付新消息费用」）。

**明确不能做的替代**：不得用编译结果顶替业务验收（`iteration.md:733`「这些只证明编译，不是事务、业务或页面验收」）；不得用构建结果顶替部署或实群通过（`operations.md:455`、`deploy/linux/README.md:3`）。

---

## 需要维护者决定的事项（不代替决定）

### 许可证与素材

1. **代码许可证选择**（未决）。仓库当前处于“公开但无许可”状态；`docs/LenBot_成熟开源项目路线书_20260921.md:157` 明确「不把"仓库公开"当完整开源许可」。
2. **AGPL-3.0 vendored 插件与整体打包的关系**：`asoul_calendar` 单独保留 AGPL 原件，但与其他代码同 wheel、同镜像。
3. **`font.ttf` 的处置**：上游无许可文件，且已被 3 个插件复用。选择包括替换为已知许可字体、取得授权或移除。
4. **`cards/bilibili/logo.png`** 的品牌/商标使用依据。
5. **用户提供素材的授权取得方式**：6 份人设原文、49 张表情、5 张常服图、19 张待审表情。
6. **角色素材与发行版的关系**：是否把 A-SOUL 相关预设/素材从通用发行中剥离（对应 S0-03 的“非 A-SOUL 发行配置草案”，任务卡 `:130`）。
7. **第三方非商用素材（枝江三视图集）是否纳入**。
8. **是否建立 NOTICE / 第三方许可汇总**（当前依赖许可原文不在仓库内）。
9. **外部贡献授权声明形式**（CLA / DCO / inbound=outbound；路线书 `:629` 称「不必一开始就引入复杂 CLA」）。

### 保留政策与支持范围

10. **数据保留政策**：路线书把“保留政策”列为 S5-03 交付物（`:537`）、S0-02 未决问题（`docs/plan/s0-02-product-positioning-and-decisions.md:136`）。现状：**事件/认识/摘要/聊天媒体无保留期限**（`docs/plan/s0-01-s4-s7-inventory.md:164`）；`docs/LenBot_成熟开源项目路线书_20260921.md:424` 明确「许可证、资料保留和接口发布都是明确决定」。
11. **首个支持组合的最终选择**（从 B.3 的 C1—C3 中选一条主链）。
12. **当前不支持范围的公开措辞**：README 已区分“未配置 / 停用 / 未部署 / 待验收”，但支持矩阵文件本身尚不存在（`docs/plan/s0-01-s4-s7-inventory.md:7`）。
13. **版本与支持政策**：无 CHANGELOG、无非备份 tag（现有 3 个 tag 全为 `backup/pre-rewrite-*`）、无 release 分支。
14. **验收证据的公开程度**：脱敏后哪些进文档、哪些只留本机（路线书 `:662`）。
15. **是否以及何时修改“不运行测试”的工程约束**（路线书 `:568,720`：未来需要自动验证时「先单独讨论修改工程规则」）。

---

## 未确认与读不到

1. **`uv.lock` 不含 license 字段**（`grep -c license uv.lock` = 0）；A.2 的许可值实际读自本机 `.venv` 的 `dist-info/METADATA`。**许可证原文不随仓库分发**，且未联网核对 PyPI 页面。
2. **`aiosqlite`、`jinja2`、`qrcode`、`python-dateutil`** 只有 classifier、没有明确的 `License` / `License-Expression` 字段——应视为“按 classifier 推断”，需补读原文。
3. **`pyproject.toml` 无 `license` 字段**，故无法从项目元数据确认任何代码许可；`METADATA` 亦无。
4. **`cards/schedule/{__init__.py,avatars.py}`、`cards/theme.py` 的 “reference plugin” 未具名**，无法确认其确切上游与许可（代码内只有“reference plugin”字样）。
5. **`media/`（2.0GB，2800 + 313 文件）与 `media/schedule-avatars/` 的来源无任何仓库内记录**；仓库内零命中，唯一引用在未入库的 `lenbot.config.json:1226-1230`。素材本身本轮未打开查看。
6. **`logo.png`、`lenbot-mark.svg` 无来源注释**，无法确认是否为本项目自创。
7. **`len_bot.db` 未读取**（按约束），因此没有从实际记录核对任何运行证据。
8. **未联网**：所有上游仓库（`LEN5010/astrbot_plugin_asoul`、`astrbot_plugin_qq_group_daily_analysis`、`KimigaiiWuyi/astrbot_plugin_gscore_adapter`、`asoul-gallery.pages.dev`）的当前许可状态未核对，只记录仓库内 `SOURCE.md` 的陈述。
9. **模型渠道无仓库内事实**：样例 `providers: []`；`docs/iteration.md` 提到的 `127.0.0.1:3000` 网关与“通道 4”是本机运维记录，**不是仓库能力**，不能作为首个支持组合的渠道依据。
10. **CI 未在本分支运行**：`ci.yml:5,7` 只监听 `master`/`main`，当前 `feat/s0-product-contract` 不在触发范围内；本轮也未运行 CI 或任何构建命令。
11. **未运行编译或构建**：本底稿的全部“通过”结论均来自历史 `docs/iteration.md` 记录与源码阅读，本轮未执行 `compileall` 或 `npm run build`（约束只允许只读 shell）。
12. **`docs/plan/README.md:91-95` 引用的 4 份 S0-01 底稿中，`s0-01-s3-plugin-inventory.md`、`s0-01-s4-s7-inventory.md`、`s0-01-remaining-work-index.md`、`s0-01-s1-chain-inventory.md` 在核对时点尚未全部入库**（`git status` 显示 S3/S4 两份为未跟踪、S1 与 remaining-work 未出现）。本底稿未依赖它们作为唯一来源，但交叉引用时应核对版本。

---

## 反向引用清单

**许可证与元数据**
- `pyproject.toml:1-51`（无 license 字段；`:4` 占位 description；`:43-45` 打包排除；`:47-51` dev 组含 pytest）
- `.venv/lib/python3.13/site-packages/len_bot-0.1.0.dist-info/METADATA`（无 License 字段）
- `.venv/lib/python3.13/site-packages/*/METADATA`（各依赖许可；重点：`tld`、`certifi`、`pypdfium2`、`regex`）
- `uv.lock:1-4`（无 license 字段）
- `src/len_bot/plugins/builtin/asoul_calendar/LICENSE`、`src/len_bot/plugins/builtin/group_summary/LICENSE.upstream`

**来源说明**
- `src/len_bot/plugins/builtin/asoul_calendar/SOURCE.md`（`:3,5,7`）
- `src/len_bot/plugins/builtin/asoul_dynamics/SOURCE.md`（`:1-3,9`）
- `src/len_bot/plugins/builtin/group_summary/SOURCE.md`（`:3`）
- `src/len_bot/plugins/builtin/gscore_adapter/SOURCE.md`（`:1-3,7,9`）
- `src/len_bot/plugins/builtin/asoul_calendar/calendar.py:3-4`、`render.py:1-5`
- `src/len_bot/cards/schedule/__init__.py:16`、`avatars.py:3`、`cards/theme.py:9-11`
- `src/len_bot/cognition/diana.py:1-2`

**素材**
- `docs/persona/diana/README.md:5,22`、`docs/persona/diana/sticker-pack-20260906.json:1-3`
- `docs/persona/asoul/README.md`（本批交付/来源与取舍/预览与采用）
- `docs/persona/asoul/reference-images/20260921/README.md:3,15`
- `docs/persona/asoul/sticker-review-20260921.md:3`
- `src/len_bot/plugins/builtin/asoul_calendar/resources/font.ttf`、`src/len_bot/cards/bilibili/logo.png`、`src/len_bot/web/frontend/src/assets/lenbot-mark.svg`
- `.gitignore`（`/media/`、`/src/len_bot/web/static/dist/`、`/lenbot.config.json`）

**验收边界**
- `AGENTS.md:14,16,20,21,23`
- `docs/LenBot_分阶段任务卡_20260921.md:11,141-160,183-202,1215-1233`
- `docs/LenBot_成熟开源项目路线书_20260921.md:566-570,641-660,662,680,696-707,720,781`
- `docs/LenBot_群聊体验与可靠执行_完整改造计划_20260920.md:330,595-604,648-681`
- `docs/operations.md:13-19,21,54,59,129,257-258,334-343,391,455`
- `docs/iteration.md:27,181,189,321,481,733-737,875,914-951,981-988`
- `docs/serious-issue.md:12,17,21`

**构建入口**
- `README.md:7-16,29-39,56`
- `.github/workflows/ci.yml:1-42`
- `deploy/linux/Dockerfile`、`deploy/linux/compose.yaml`、`deploy/linux/README.md:3,27,32-35,82,96`
- `deploy/linux/release-evidence.template.md`
- `containers/workspace/Dockerfile`、`containers/browser/Dockerfile`、`containers/media/Dockerfile`、`containers/media/README.md:1`

**配置事实**
- `lenbot.config.example.json:99-113,121,124-234`
- `gateway.config.example.json`（`public.deployment_verified: false`）
- `src/len_bot/config_store.py:49,143`、`src/len_bot/config.py:26,76`、`src/len_bot/cognition/providers.py:71-80`
- `src/len_bot/adapters/onebot.py`、`src/len_bot/adapters/file_upload.py:8-11`
- `src/len_bot/media/service.py:105`、`src/len_bot/media/files.py:169-171`
- `local_plugins/local_clock/README.md`
