---
layout: home

hero:
  name: LenBot
  text: 基于大模型的 QQ 群聊机器人
  tagline: 通过 OneBot v11 接入 QQ，模型服务自己选择，设置和管理都在网页面板里完成。
  image:
    src: /lenbot-mark-animated.svg
    alt: LenBot
  actions:
    - theme: brand
      text: 快速开始
      link: /guide/quick-start
    - theme: alt
      text: 编写插件
      link: /develop/plugins
    - theme: alt
      text: GitHub
      link: https://github.com/lendevs/LenBot

features:
  - title: 群聊
    details: 每个群各有一段连续的对话。是否接话、回复哪一条由模型判断，回复时可以引用原消息、@ 群友或者只发一个表情。
  - title: 角色
    details: 人物设定和说话方式写在角色包里。默认角色小然安装后即可使用，修改后可以先试聊，满意再保存。
  - title: 记忆
    details: 记忆是保存在本地的 Markdown 文件，每个群分开存放。面板里可以查看和修改，也可以让 Bot 彻底忘掉某件事。
  - title: 后台任务
    details: 写报告和整理资料这类耗时的工作在 Docker 容器里执行，不占用聊天。完成后，生成的文件会发回群里。
  - title: 插件
    details: 插件用 Python 编写，可以添加命令和定时任务，也可以给模型增加工具。修改插件设置不需要重启。
  - title: 网页面板
    details: 首次配置和之后的设置都在面板里完成。主人也可以在群里直接让 Bot 修改设置。
---

<div class="home-section">
  <h2>从安装到进群</h2>
  <ol class="steps">
    <li><strong>安装</strong><span>用部署包或 Docker 安装，也可以从源码运行。三种方式运行的是同一个程序。</span></li>
    <li><strong>首次配置</strong><span>启动后在浏览器里打开终端给出的链接，按向导连接 QQ 并选择模型。</span></li>
    <li><strong>试聊</strong><span>先用模拟发送，在面板的对话测试页和 Bot 聊几轮，回复不会发到 QQ。</span></li>
    <li><strong>进群</strong><span>效果满意后改为真实发送，在群里 @ Bot 试一下。</span></li>
  </ol>
</div>

<div class="home-section">
  <div class="box">
    <h2>当前状态</h2>
    <p>首个公开版本 0.2.0 尚未发布，部署包和 Docker 镜像要等发布后才能下载。在此之前请<a href="./guide/install-source">从源码运行</a>。</p>
    <p>目前只支持 QQ，需要另外运行一个 OneBot 实现（例如 NapCat），并准备自己的模型服务。提示词和面板只有中文。</p>
  </div>
</div>

<div class="shots">
  <figure class="wide"><img src="/screenshots/home.png" alt="面板首页，显示 QQ 连接状态、今天的消息量和群列表"><figcaption>面板首页。这里显示 QQ 的连接状态和今天的消息量，以及模型用量和需要处理的事项。</figcaption></figure>
  <figure><img src="/screenshots/persona.png" alt="角色编辑页"><figcaption>角色编辑页。修改设定和样例后可以先试聊。</figcaption></figure>
  <figure><img src="/screenshots/plugins.png" alt="插件页的插件目录"><figcaption>插件目录。安装后可以选择在哪些群启用。</figcaption></figure>
</div>
