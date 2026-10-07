---
layout: home
hero:
  name: LenBot
  text: 长期待在 QQ 群里的聊天 Agent
  tagline: 每个群一份持久会话，自己判断什么时候开口。自带网页面板，管角色、记忆、插件和后台任务。
  image:
    src: /lenbot-mark.svg
    alt: LenBot
  actions:
    - theme: brand
      text: 快速开始
      link: /guide/quick-start
    - theme: alt
      text: 在 GitHub 上查看
      link: https://github.com/lendevs/LenBot
features:
  - title: 群聊
    details: 每个群或私聊都有一份一直存在的会话。它自己判断回不回、回谁，会用表情、引用和提及；长对话自动压缩成回想。
  - title: 角色
    details: 设定、说话方式、底线、样例、知识和表情装在一个角色包里，可以导入导出，改动前先开草稿试聊。
  - title: 记忆
    details: 本地 Markdown 记忆，按群分开存。全文检索，可选向量检索；可以在面板里修改、删除，也可以彻底遗忘。
  - title: 后台任务
    details: 写报告、整理资料这类长活交给独立 Docker 容器里的 Pi，聊天不用等。可以追问、取消和续接，产物登记后发到群里。
  - title: 插件
    details: 同进程 Python 插件，支持命令、规则、定时、工具和技能。从 Git 或 ZIP 安装，有版本检查，单个插件重载不打断聊天。
  - title: 面板
    details: 首次配置向导、模型和预算、权限、日志、更新与恢复都在网页里完成。主人也可以在群里用一句话改设置。
---

<div class="stage">
  <div class="box">
    <h2>现在的阶段</h2>
    <p>首个公开版本 0.2.0 正在准备，还没有发布。发布前可以<a href="./guide/install-source">从源码运行</a>；部署包和 Docker 镜像会在发布时提供。</p>
    <p>目前只接 QQ（通过 OneBot v11），提示词和面板只有中文，不提供语音合成。</p>
  </div>
</div>

<div class="shots">
  <figure class="wide"><img src="/screenshots/home.png" alt="面板首页：连接状态和各群概况"><figcaption>首页：连接状态、各群今天的情况和最近的错误。</figcaption></figure>
  <figure><img src="/screenshots/persona.png" alt="角色编辑页"><figcaption>角色：设定、说话方式和样例，改完先试聊。</figcaption></figure>
  <figure><img src="/screenshots/plugins.png" alt="插件页"><figcaption>插件：安装、配置，在详情里直接选在哪些群用。</figcaption></figure>
</div>
