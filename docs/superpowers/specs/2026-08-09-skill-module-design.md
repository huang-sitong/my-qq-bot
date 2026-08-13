# Skill 模块（提示词包技能，类 Claude Code）

日期：2026-08-09
状态：已批准（2026-08-09）

## 背景

目标：为 bot 引入「技能」概念——把**某个能力的完整提示词包**（描述 + 正文）打包成一个可插拔模块，LLM 根据描述**自主判断**何时启用。这是当前架构缺失的一环：能力扩展只有两条路——写死在图里的工具 + MCP 外部工具，都没有「按需加载一套提示词并跨轮生效」的机制。

现状核实：

- **SystemMessage 是四层动态注入**（persona / 当前时间 / 对话摘要 / 记忆或 MCP 提示），由 `build_system_messages`（`bot/core/utils/context.py`）单一来源构造，`estimate_context_tokens` 复用同一函数保证 token 估算与实际注入永不偏离（CLAUDE.md 硬性约束）。技能层必须走这条链路，否则估算偏离。
- **工具统一层**（`build_tools` → `list[BaseTool]`）已把 RAG / 记忆 / MCP 归一，由 prebuilt `ToolNode` 执行，`handle_tool_errors` 降级。技能加载工具直接复用此管线。
- **图结构**：`call_llm` 条件边 → `tools`（ToolNode）→ 回边 `call_llm`；`tools → call_llm` 的边上插入新节点最自然。
- **ToolNode 只能返回 ToolMessage、不能改图 state**——「技能激活跨轮持续」需要一条写回路径。方案：图内 state 字段 + `skill_manager` 节点（与 `index_turn`/`summarize` 同构的 action_node）。

## 决策（用户已确认）

1. **形态 = 提示词包技能**：每个技能一个目录 `skills/<name>/SKILL.md`（YAML frontmatter `name` + `description`，正文任意长 Markdown）。**单文件、无参考资源附件**——正文不够时靠现有 RAG / 记忆 / MCP 工具补。
2. **发现机制 = 索引 + 按需加载（类 Claude Code）**：系统提示只注入技能索引（name + 一句话 description，很小），LLM 判断某技能适用时调用 `load_skill` 工具取回正文再执行。
3. **纯提示词、不带专属工具**：技能执行靠现有工具（RAG 检索 / 用户记忆 / MCP）+ 正文指引。架构改动最小。
4. **管理 = 文件即技能**：开发者写 `skills/` 目录，启动时 `SkillRegistry` 扫描加载，改后重启生效。
5. **存续 = 会话内持续**：技能激活后写入 `BotState.active_skills`（checkpoint 按 thread 持久化），后续轮次持续注入正文，直到 LLM 调用 `unload_skill` 释放或话题偏离后 LLM 自行释放。
6. **方案 A = 图内状态 + `skill_manager` 节点**（对比：B 存 `AsyncSqliteStore` 需 `build_system_messages` 改 async、状态与 checkpoint 分离；C 从消息历史推断会被 `summarize` 裁剪破坏——均否决）。

## 文件变更

| 文件 | 动作 | 说明 |
|---|---|---|
| `skills/` | 新增 | 技能存放目录（`BOT_SKILLS_DIR`，默认 `"skills"`），每个技能一个 `<name>/SKILL.md` |
| `bot/core/skills/__init__.py` | 新增 | 导出 `SkillRegistry`、`load_skill` / `unload_skill` |
| `bot/core/skills/loader.py` | 新增 | `SkillRegistry`：启动扫描目录 → 解析 frontmatter → 构建 `{name: {description, body}}` 索引；`names()` / `index_lines()` / `get_body(name)` |
| `bot/core/skills/tools.py` | 新增 | `load_skill(skill_name)` / `unload_skill(skill_name)` 纯函数（校验存在性、返回正文/确认，不写状态） |
| `bot/core/nodes/action_node/skill_manager.py` | 新增 | `skill_manager_node`：从 `state["messages"]` 找最后带 tool_calls 的 AIMessage → 更新 `active_skills` |
| `bot/core/nodes/__init__.py` | 改 | 导出 `skill_manager_node` |
| `object/bot/state.py` | 改 | `BotState` 加 `active_skills: list[str]`（只增不改） |
| `bot/core/utils/context.py` | 改 | `build_system_messages` 与 `estimate_context_tokens` 加 `skill_registry=None, active_skills=None` 透传，注入技能索引 + 激活正文两层 |
| `bot/core/nodes/llm_node/call_llm.py` | 改 | 从 state 取 `active_skills`，把 `skill_registry` 传入 `build_system_messages` |
| `bot/core/nodes/action_node/summarize.py` | 改 | 把 `skill_registry` 透传给 `estimate_context_tokens`（保持估算一致） |
| `bot/core/tools/factory.py` | 改 | `build_tools(..., skill_registry=None)` → 注入 `load_skill` / `unload_skill` |
| `bot/core/graph.py` | 改 | 注册 `skill_manager` 节点；边改为 `tools → skill_manager → call_llm`；透传 `skill_registry` |
| `common/config.py` | 改 | 加 `skills_enabled` / `skills_dir` / `skills_index_max` |
| `common/prompts.py` | 改 | 加 `SKILL_INDEX_HINT` / `SKILL_ACTIVE_HINT` 模板 |
| `bot/handler.py` | 改 | 初始 state 注入 `"active_skills": []`（一行） |
| `main.py` | 改 | 构建 `SkillRegistry` → 传入 `create_graph` |
| `.env-template` | 改 | `BOT_SKILLS_ENABLED` / `BOT_SKILLS_DIR` / `BOT_SKILLS_INDEX_MAX` |
| `CLAUDE.md` | 改 | 新增技能模块段、SystemMessage 层级、env 清单 |
| `tests/fakes.py` | 改 | 加 `StubSkillRegistry` |
| `tests/test_skill_loader.py` | 新增 | frontmatter 解析、非法/重复跳过、名字校验、索引截断 |
| `tests/test_skill_tools.py` | 新增 | load_skill 返回正文/不存在报错、unload_skill 幂等、schema 排除注入参数 |
| `tests/test_skill_manager.py` | 新增 | 节点从 tool_calls 正确增删 `active_skills`（load/unload/混合/no-op） |
| `tests/test_context.py` | 改 | 注入索引+正文层；估算与实际注入一致 |
| `tests/test_graph.py` | 改 | 集成：同 thread 二次 ainvoke 后 `active_skills` 仍持久化；新 thread 不串 |

## 配置（`BotConfig` 新增）

```python
skills_enabled: bool   # BOT_SKILLS_ENABLED，默认 1（"0"/"false"/"False"/"" 视为关）
skills_dir: str        # BOT_SKILLS_DIR，默认 "skills"
skills_index_max: int  # BOT_SKILLS_INDEX_MAX，默认 50（索引注入条数上限，防上下文膨胀）
```

`skills_enabled=0` 时：不构建注册表、索引/激活正文两层都不注入、`load_skill`/`unload_skill` 不进工具列表——等效功能关闭，零运行时开销。

## 技能格式与索引

```markdown
---
name: translate
description: 中英互译，保留语气与格式。用户说「翻译/译/翻一下」或贴外文时启用。
---

# 翻译技能
## 使用方式
- 识别用户给的语言，翻译成目标语言（用户未指明则中↔英）
- ...
## 规则
- 不要意译专业术语……
```

- `name` 只允许 `[a-z0-9_-]`，作为 `load_skill` 参数与目录名。
- `description` 是 LLM 判断「何时启用」的唯一依据——**必须写清触发信号**。
- frontmatter 缺失/非法 → 跳过该技能 + warning；`name` 非法/重复 → 非法跳过、重复取最后一个 + warning。
- 同目录多文件只读 `SKILL.md`。

## SystemMessage 层级（`build_system_messages`）

```
persona（恒为 messages[0]）
当前时间提示（CURRENT_TIME_HINT）
对话摘要（来自 summarize_node）
技能索引层     ← 新增：SKILL_INDEX_HINT + 索引（超 skills_index_max 截断，末尾提示「…共 N 个技能，仅显示前 M 个」）
已激活技能正文层 ← 新增：按 active_skills 从磁盘重读 SKILL.md 正文拼入（正文永远新鲜，checkpoint 只存名字）
记忆工具提示（MEMORY_TOOL_HINT，仅 memory_store 注入时）
MCP 工具提示（MCP_TOOL_HINT，仅 mcp_tools 非空时）
```

`estimate_context_tokens` 复用 `build_system_messages`，技能层 token 估算与实际注入永不偏离。注册表为空 / 无激活技能 → 对应层跳过。单技能正文读取失败 → 跳过该技能注入，不崩轮。

## 图结构与数据流

```
改后：  detect_intent → describe_image → call_llm ──┬─ tools（ToolNode）──→ skill_manager ──→ call_llm（回边）
                                                   └─ summarize → index_turn → END（无 tool_calls）
```

- **`skill_manager_node`**（action_node，与 `index_turn` 同构）：
  - 从 `state["messages"]` 末尾向前找到最后一个带 `tool_calls` 的 AIMessage；
  - `load_skill(name)`：校验存在于注册表后 `active_skills += [name]`；`unload_skill(name)`：移除；
  - 无技能调用 / 名字非法 → 返回 `{}` no-op，不打断正常工具循环。
- **技能工具不需要 `thread_id` 注入**：`load_skill`/`unload_skill` 只返回正文/确认（纯函数），状态写回全在 `skill_manager` 节点内从 state 读——比记忆工具还简单，无 `InjectedState`。

数据流全链路：

```
call_llm 返回带 load_skill("translate") 的 AIMessage
  → tools（ToolNode 执行，正文作为 ToolMessage 本轮立即可见）
  → skill_manager（把 "translate" 写入 active_skills）
  → call_llm 回环（此时系统层已注入翻译技能正文）
下一轮新消息（同 thread）：checkpoint 恢复 active_skills=["translate"]
  → build_system_messages 重新注入正文 → 技能持续生效
用户说「不用翻译了」→ LLM 调 unload_skill → active_skills 清空 → 正文不再注入
```

`detect_intent`、`describe_image`、`summarize`、`index_turn` 均不受影响；`tool_rounds` 上限（`rag_max_agent_rounds`）天然保护技能加载循环。

## 错误处理

| 场景 | 行为 |
|---|---|
| `skills/` 目录不存在 | 注册表为空，索引/工具都不注入，等效功能关闭 |
| SKILL.md frontmatter 缺失/非法 | 跳过该技能 + warning 日志 |
| `name` 非法 / 重复 | 非法跳过 / 重复取最后一个 + warning |
| `load_skill` 传不存在的名字 | 工具返回「技能 'xx' 不存在。可用：…」供 LLM 纠正；`skill_manager` 再次校验拒绝写入 |
| `unload_skill` 未激活的技能 | 幂等，返回确认 |
| 技能正文读取失败 | 注入层静默跳过，不崩轮 |
| 技能已激活但文件被删 | 注入层静默跳过，`active_skills` 保留脏名（下一轮 LLM 会 unload） |
| 工具执行异常 | 走既有 `build_tools` 降级路径（ToolInvocationError 透传、其余占位文案「工具执行失败。」） |
| 索引超 `skills_index_max` | 截断注入，末尾提示「…共 N 个技能，仅显示前 M 个」 |

## 测试

| 测试 | 断言 |
|---|---|
| `test_skill_loader.py` | frontmatter 解析（name/description/正文）、非法跳过、名字校验、目录扫描、索引截断 |
| `test_skill_tools.py` | load_skill 返回正文 / 不存在报错、unload_skill 幂等、schema 不含注入参数 |
| `test_skill_manager.py` | 节点从 AIMessage tool_calls 增删 `active_skills`：load / unload / 混合调用 / no-op 四种 |
| `test_context.py`（扩展） | `build_system_messages` 注入索引+正文层（含空/截断/读取失败）；`estimate_context_tokens` 估算与实际注入一致（硬约束回归） |
| `test_graph.py`（扩展） | fake LLM 先调 `load_skill` 再作答 → 同 thread 二次 `ainvoke` 后 `active_skills` 仍在（checkpoint 持久化）；新 thread 不串 |

`tests/fakes.py` 加 `StubSkillRegistry`（内存 {name: {description, body}}），不读真实磁盘。

## CLAUDE.md 同步

- **数据流**：`tools → skill_manager → call_llm` 回环（技能激活写回 state）。
- **SystemMessage 层级**：摘要之后插入技能索引层 + 激活正文层；`build_system_messages` / `estimate_context_tokens` 签名加 `skill_registry` / `active_skills`。
- **新增 `bot/core/skills/`**：SkillRegistry（启动扫描、frontmatter 解析）、load/unload 工具（纯函数，状态写回在 skill_manager）。
- **Node type convention**：`skill_manager` 为 action_node（确定性、无 LLM）。
- **env 清单**：`BOT_SKILLS_ENABLED` / `BOT_SKILLS_DIR` / `BOT_SKILLS_INDEX_MAX`。

## 风险

- **LLM 能否主动调用 load_skill**：sensenova 的 tool-calling 对「先取技能正文再执行」的两段式是否稳定，需联调确认。降级兜底：技能描述写得足够具体（含明确触发信号），且 load_skill 失败不崩轮（占位文案 + skill_manager no-op）。
- **`tool_rounds` 共享预算**：技能加载 + 执行 + RAG/MCP 共用 `rag_max_agent_rounds` 预算；单技能加载占 1 轮，多技能/深度场景可能需调大，实现后按需评估。
- **索引膨胀**：技能数超过 `skills_index_max` 时只显示前 N 条，靠 `load_skill` 的错误提示兜底（列出可用技能）——但被截断的技能 LLM 仍可通过工具错误提示发现，属可接受降级。
