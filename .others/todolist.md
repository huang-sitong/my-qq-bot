# 架构改造 Todolist

> 目标：在现有单机 QQ Bot 基础上，按 DDD 限界上下文思想做架构演进，保持测试通过。

## 阶段 0：现状盘点
- [x] 梳理当前项目结构与模块职责
- [x] 识别已有分层：transport / core / common / domain
- [x] 确定主要限界上下文候选：协议接入、消息路由、会话编排、命令、技能、RAG、记忆、视觉、工具集成

## 阶段 1：端口与适配器（Ports & Adapters）
- [x] 定义 `MessageQueue` 端口，`MessageWorkerPool` 依赖抽象队列而不是直接 `asyncio.Queue`
- [x] 定义 RAG/文件发送等端口，核心流程面向接口
- [x] 定义记忆/视觉等端口，核心流程面向接口
- [x] 将 `CommandServices` 从 domain 移到 application 层（保留兼容导出）

## 阶段 2：事件驱动与可靠性
- [x] 增加 `event_id` 幂等去重，防止重复事件导致重复回复/重复索引
- [x] 为消息队列增加可替换适配器（通过 `queue_factory` 注入，当前默认 asyncio.Queue）
- [x] 为外部服务增加统一重试/降级策略（`retry_async` + 既有降级；熔断可按需后续引入）
- [x] 增加 trace_id/event_id 结构化日志贯穿（ContextVar + log filter）

## 阶段 3：限界上下文包结构
- [x] 将 `src/bot/transport` 收敛为 `protocol` 上下文
- [x] 将 `src/bot/core/rag`、`memory`、`vision`、`skills`、`commands` 收敛为独立上下文包
    - [x] rag -> knowledge
    - [x] memory -> memory
    - [x] vision -> vision
    - [x] skills -> skill
    - [x] commands -> commands
- [x] 将 `src/domain` 按上下文拆分：commands/skill/vision/knowledge/conversation 领域模型均已迁移，`domain` 保留兼容导出
- [x] 保留旧导入路径作为兼容层，逐步迁移

## 阶段 4：状态与资源治理
- [x] 清理 `thread_id` 对应的 lock / auto_reply 状态，避免长期运行无界增长
- [x] 统一数据库生命周期与所有权（main.py 统一创建/关闭，各库 owner 已在 AGENTS 明确）
- [x] 增加可观测性指标（队列深度、丢弃数、活跃 thread 数）
- [x] 增加处理耗时指标（总耗时 + route/dispatch 分阶段平均耗时）

## 验证
- [x] 基线测试通过（518 passed, 1 skipped）
- [x] 每次改造后运行 `uv run python -m pytest -q`（当前 520 passed, 1 skipped）
- [x] 更新 README/AGENTS 架构说明

## 状态
- [x] 架构改造已完成，验证通过（520 passed, 1 skipped；ruff check passed）
