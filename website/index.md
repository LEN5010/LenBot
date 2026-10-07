---
layout: home
hero:
  name: LenBot
  text: 住在 QQ 群里的聊天 Agent
  tagline: 每个群一份持久会话，自己判断什么时候开口。角色、记忆、插件和后台任务都在网页面板里管。
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
    details: 每个群或私聊一份一直存在的会话。它自己决定回不回、回谁，会发表情、引用和 @ 人；聊得太长会压缩成回想。
  - title: 角色
    details: 设定、说话方式、底线、样例、资料和表情放在一个角色包里。改动先开草稿试聊，满意再保存。
  - title: 记忆
    details: 记忆是本地的 Markdown 文件，按群分开。默认全文检索，可以接向量服务；面板里能改、能删、能彻底遗忘。
  - title: 后台任务
    details: 写报告、整理资料这类长活交给独立 Docker 容器里的 Pi 去做，聊天不用等。能追问、取消、续接，做完把文件发回群里。
  - title: 插件
    details: 同进程的 Python 插件，写命令、规则、定时、工具和技能。从 Git 或 ZIP 安装，单个插件重载不打断聊天。
  - title: 面板
    details: 首次配置、模型和预算、权限、日志、更新与恢复都在网页里完成。主人也可以直接在群里让它改设置。
---

<div class="stage">
  <div class="box">
    <h2>现在的状态</h2>
    <p>首个公开版本 0.2.0 还在准备，部署包和 Docker 镜像会随它一起发布。现在可以<a href="./guide/install-source">从源码运行</a>。</p>
    <p>目前只接 QQ（通过 OneBot v11），提示词和面板只有中文，不做语音合成。</p>
  </div>
</div>

<div class="shots">
  <figure class="wide"><img src="/screenshots/home.png" alt="面板首页：连接状态和各群概况"><figcaption>首页：连接状态、各群今天的情况和最近的错误。</figcaption></figure>
  <figure><img src="/screenshots/persona.png" alt="角色编辑页"><figcaption>角色：设定、说话方式和样例，改完先试聊。</figcaption></figure>
  <figure><img src="/screenshots/plugins.png" alt="插件页"><figcaption>插件：安装、配置，在详情里直接选在哪些群用。</figcaption></figure>
</div>
