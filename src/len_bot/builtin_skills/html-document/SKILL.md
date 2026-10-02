---
name: html-document
description: 制作可在手机浏览或打印的本地 HTML 活动单、报告和表格，用任务镜像的离线渲染工具生成实际 PDF 与截图。
---

# HTML 文档

从用户实际给出的数据组织页面，先保存数据，再据此排版。没有给出的主办方、参与规则、报名条件等不属于排版素材；无需为了凑栏目补齐内容。

使用内联 CSS 和本地图片，输出到 `/workspace/out/`。按内容需要设计屏幕布局和 `@media print`；`@page` 指定打印纸张与边距，没有指定时渲染器使用 A4。

```sh
lenbot-render out/document.html --pdf out/document.pdf --screenshot out/desktop.png --print-preview out/print
lenbot-render out/document.html --screenshot out/mobile.png --width 390 --height 844
```

`--help` 查看用法。JSON 返回实际路径、PDF 页数和各页的打印预览，无需另行查页数或转换同一个 PDF。查看需要的屏幕截图和打印预览，核对内容、裁切和字号。只做 HTML 时，PDF／截图可留作本任务检查材料，不必全部交付。命令失败按原错处理，已有文件仍留在工作区。

用 `deliver_file` 登记用户需要的文件；说明实际完成及未确认部分，文件登记不是平台上传。
