# 消息队列改造 Todo

## Task 1: 移除 cooldown，添加队列基础设施
- [x] `__init__` 中添加 `asyncio.Queue`、`_locks`、`_worker_task`
- [x] 删除 `_cooldowns: dict[str, float]`
- [x] 简化 `session_id` 为 `{platform}:{channel}:{user}`（去掉 guild_id）
- [x] 删除 `_on_cooldown` 方法
- [x] 删除 `handle()` 中的冷却检查块
- [x] 添加 `start()` 和 `stop()` 生命周期方法
- [x] 添加 `_worker()` 后台处理循环
- [x] 语法检查
- [ ] Commit

## Task 2: 拆分 handle() 和 _process()
- [x] `handle()` 改为仅验证 + 入队
- [x] 新增 `_process()` 方法，包含真正的处理逻辑（路由→graph→回复→记忆）
- [x] 语法检查
- [ ] Commit

## Task 3: 接线 main.py
- [x] handler 创建后调用 `await handler.start()`
- [x] finally 块中调用 `await handler.stop()` 再断开连接
- [x] 语法检查
- [ ] Commit

## Task 4: 更新 CLAUDE.md
- [x] 更新 handler 描述（cooldown → queue）
- [x] 更新 session_id 格式
- [x] 更新数据流图
- [ ] Commit

---

## 验收
- [ ] 启动后私聊消息正常回复
- [ ] 同频道快速连发多条消息，全部处理（不掉消息）
- [ ] 两个不同频道同时发消息，都收到回复（不阻塞）
- [ ] Ctrl+C 关闭时 worker 排空队列后正常退出
- [ ] 删除旧 `db/checkpoint.sqlite`，验证新 thread_id 格式为 `platform:channel_id`
