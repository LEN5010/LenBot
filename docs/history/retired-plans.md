# 已退役计划与旧底稿回查

2026-09-26 清理时，将已被当前合同取代的计划和静态盘点从工作树移除。原文完整保存在 Git 提交 `66d02250cfd9e24b9ec1d1f3d69b26230f148aab` 中；无需切换工作区、恢复旧代码或重建旧文件。真实运行记录、失败原文和人物素材继续保留在各自原目录。

从仓库根目录读取，例如：

```sh
git show 66d02250cfd9e24b9ec1d1f3d69b26230f148aab:'docs/LenBot_分阶段任务卡_20260921.md'
```

将冒号后的路径替换为下列原路径即可。旧文件中的“当前”、行号、缺口和授权只对原批次负责；当前状态见[路线入口](../plan/README.md)，行为见[文档入口](../README.md)。原 B／M／P 与 A01—A30 合同仍在[原验收依据](../LenBot_群聊体验与可靠执行_完整改造计划_20260920.md)。

## roadmap

产品路线书；目标、阶段顺序和设计理由。

原路径：`docs/LenBot_成熟开源项目路线书_20260921.md`。

## task-cards

48 项原任务卡；原范围、依赖与验收要求。

原路径：`docs/LenBot_分阶段任务卡_20260921.md`。

## natural-chat

N00—N06 设计与当批交接；素材原件继续保留。

原路径：`docs/LenBot_自然群聊与能力调度_改进计划_20260921.md`。

## baseline-index

9178c34 基线剩余工作索引。

原路径：`docs/plan/s0-01-remaining-work-index.md`。

## baseline-s1

S1 主链的旧源码行号与缺口盘点。

原路径：`docs/plan/s0-01-s1-chain-inventory.md`。

## baseline-s2

S2 上下文的旧源码行号与缺口盘点。

原路径：`docs/plan/s0-01-s2-context-inventory.md`。

## baseline-s3

S3 插件合同的旧源码行号与缺口盘点。

原路径：`docs/plan/s0-01-s3-plugin-inventory.md`。

## baseline-s4-s7

S4—S7 的旧源码与文档存在性盘点。

原路径：`docs/plan/s0-01-s4-s7-inventory.md`。

## dependency-boundary

旧依赖测绘、个人预设与通用配置草案。

原路径：`docs/plan/s0-03-dependency-boundary.md`。

## authorization

2026-09-21 的当批操作授权；不作为当前授权。

原路径：`docs/plan/authorization.md`。

## delegation

前期分工与任务安排；不作为当前实施顺序。

原路径：`docs/plan/delegation-plan.md`。

## 已压缩文件的原文

同一提交还可读取以下文件清理前的全文：

- `docs/plan/README.md`：48 项状态及当时长篇进度说明。
- `docs/plan/s0-02-product-positioning-and-decisions.md`：决策论证及旧基线。
- `docs/plan/s0-04-06-license-and-support.md`：当时本机依赖许可、素材与支持清点。
- `docs/plan/s2-01-conversation-segment.md`：字段草案与逐批实施补记。
- `docs/iteration.md`：9 月 25 日收尾、编译／构建范围和完整现场清单。

已确认决定与未决事项已提取至当前文档；旧行号、私有运行目录数量、未入库文件状态和当批授权不继续作为当前事实维护。
