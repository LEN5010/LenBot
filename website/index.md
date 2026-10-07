---
layout: home
hero:
  name: LenBot
  text: 长期待在 QQ 群里的聊天机器人
  tagline: 不用敲命令，它自己判断什么时候该接话。角色、记忆、插件和后台任务都在网页面板里管。
  image:
    src: /lenbot-mark.svg
    alt: LenBot
  actions:
    - theme: brand
      text: 快速开始
      link: /guide/quick-start
    - theme: alt
      text: 写插件
      link: /develop/plugins
    - theme: alt
      text: GitHub
      link: https://github.com/lendevs/LenBot
features:
  - title: 群聊
    details: 每个群有一段一直延续的对话。它自己决定回不回、回谁，会引用、会 @ 人、会发表情，聊久了把早先的内容整理成回想。
  - title: 角色
    details: 设定、说话方式、底线、样例、资料和表情放在一个角色包里。默认角色小然装好就能用，改动先试聊，满意再保存。
  - title: 记忆
    details: 记忆是本地的 Markdown 文件，每个群分开存。面板里能看、能改，也能让它彻底忘掉某件事。
  - title: 后台任务
    details: 写报告、整理资料交给 Docker 容器里的后台任务去做，聊天不用等。可以追问和取消，做完把文件发回群里。
  - title: 插件
    details: 用 Python 写插件，加命令、定时任务和给模型用的工具。从 Git 或 ZIP 安装，改插件设置不用重启。
  - title: 面板
    details: 首次配置、模型、权限、日志、升级和恢复都在网页里做。主人也可以直接在群里让它改设置。
---

<div class="stage">
  <div class="box">
    <h2>现在能用吗</h2>
    <p>能用，不过首个公开版本 0.2.0 还在准备，部署包和 Docker 镜像要等它发布。现在请<a href="./guide/install-source">从源码运行</a>。</p>
    <p>目前只接 QQ，需要另外运行一个 OneBot 实现（比如 NapCat）和你自己的模型服务。提示词和面板只有中文。</p>
  </div>
</div>

<div class="shots">
  <figure class="wide"><img src="/screenshots/home.png" alt="面板首页：连接状态和各群概况"><figcaption>首页：连接状态、各群今天的情况和最近的错误。</figcaption></figure>
  <figure><img src="/screenshots/persona.png" alt="角色编辑页"><figcaption>角色：设定、说话方式和样例，改完先试聊。</figcaption></figure>
  <figure><img src="/screenshots/plugins.png" alt="插件页"><figcaption>插件：安装、配置，在详情里直接选在哪些群用。</figcaption></figure>
</div>
