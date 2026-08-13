# Context Management Design

Date: 2026-07-30
Status: approved

## Context

The QQ bot currently has **no context management** — messages accumulate
unbounded via `add_messages` reducer in `BotState`, stored in
`checkpoint.sqlite`. Each `call_llm` invocation sends the **entire** message
history to the LLM. There is no token counting, no trimming, no summarization.

The default model (`sensenova-6.7-flash-lite`) is small; long conversations will
eventually exceed its context window, causing API errors or degraded responses.

This design adds **progressive summarization with a sliding window** to keep
context within the model's limits while preserving key information.

## Design Overview

Three-layer context structure, dynamically injected each `call_llm` invocation
(not persisted in checkpoint):

```
[0] SystemMessage(persona)              ← role / persona (static)
[1] SystemMessage(user_memories)        ← long-term user facts from MemoryStore
[2] SystemMessage(conversation_summary) ← progressive summary of older messages
[3..N] Recent messages                  ← sliding window (most recent N tokens)
```

When the total token count exceeds the trigger threshold, older messages are
compressed into `conversation_summary`. The summary is incremental — each new
summary merges the prior summary with the newly compressed messages.

### Graph Changes

```
Current:     START → detect_intent → router → call_llm → END
Proposed:    START → detect_intent → router → call_llm → summarize → END
                                                         ↘ END (skip)
```

A new `summarize` action_node runs after `call_llm`. It checks token count and,
if over threshold, generates a summary and trims old messages. The node is
an **action_node** because the check is deterministic; only the summary
generation calls an LLM.

### Async Strategy

Summary generation happens **after** the reply is returned to the user
(`call_llm` → user reply → `summarize`). This means the user does not wait for
summarization to complete. The summary is available for the *next* invocation.

## New Files & Components

### `common/config.py` — BotConfig additions

```python
# --- Context Window ---
llm_context_window: int = 200_000
    # Maximum context window in tokens for the LLM model.

summary_trigger_ratio: float = 0.6
    # Fraction of context_window at which summarization triggers.
    # e.g. 0.6 × 200K = 120K tokens.

summary_keep_ratio: float = 0.2
    # Fraction of context_window to retain as the sliding window after trimming.
    # e.g. 0.2 × 200K = 40K tokens of recent messages.

summary_max_input_tokens: int = 8_000
    # Maximum tokens to send to the summarization LLM call.
    # Prevents the summarization call itself from exceeding context.
```

### `bot/core/context.py` — context utilities

New module wrapping LangChain built-ins:

| Symbol | Description |
|---|---|
| `estimate_context_tokens(messages, persona, memories, summary)` | Wraps `count_tokens_approximately` with `chars_per_token=1.5` for Chinese. Sums all context layers. |
| `should_summarize(messages, persona, memories, summary, config)` | `True` when total tokens > `trigger_ratio × context_window`. |
| `prepare_summary_input(messages, old_summary, max_tokens)` | Uses `trim_messages` to limit messages passed to the summary LLM. |

Uses LangChain built-in `count_tokens_approximately` and `trim_messages` —
no custom tokenizer or manual truncation logic.

### `bot/core/nodes/action_node/summarize.py` — new summarize node

```python
async def summarize_node(state: BotState, llm: ChatOpenAI, config: BotConfig) -> dict:
    """Check context size; if over threshold, summarize old messages."""

    # 1. Check if summarization is needed
    total = estimate_context_tokens(
        state["messages"], state["persona"],
        state.get("user_memories", ""), state.get("conversation_summary", ""),
    )
    if total <= config.summary_trigger_ratio * config.llm_context_window:
        return {}  # No summarization needed

    # 2. Split messages: keep recent, summarize the rest
    keep_messages = trim_messages(
        state["messages"],
        max_tokens=int(config.summary_keep_ratio * config.llm_context_window),
        token_counter="approximate",
        strategy="last",
        start_on="human",
    )
    # Find the cutoff point — messages before this index get summarized
    keep_ids = {m.id for m in keep_messages if hasattr(m, 'id') and m.id}
    to_summarize = [m for m in state["messages"] if m.id not in keep_ids]

    if not to_summarize:
        return {}

    # 3. Generate summary via LLM
    old_summary = state.get("conversation_summary", "")
    summary_prompt = SUMMARY_PROMPT.format(
        old_summary=old_summary or "（无）",
        messages=format_messages(to_summarize),
    )
    response = await llm.ainvoke([HumanMessage(content=summary_prompt)])
    new_summary = response.content

    # 4. Remove summarized messages from state
    removes = [RemoveMessage(id=m.id) for m in to_summarize]

    return {
        "messages": removes,
        "conversation_summary": new_summary,
    }
```

### `common/prompts.py` — SUMMARY_PROMPT

```python
SUMMARY_PROMPT = """\
你是一个对话摘要助手。将对话历史压缩为一段简洁的摘要，保留关键信息。

要求：
1. **用户信息**：记录用户提到的关于自己的持久性信息（名字、偏好、背景等）
2. **讨论要点**：记录重要的讨论主题、决定、结论
3. **未完成任务**：如果有尚未完成的事项，记录下来
4. **保留语气**：如果有情绪表达或重要态度，也一并记录
5. 丢弃闲聊、重复内容、已完成的琐碎事项
6. 使用中文，不超过 1500 字

历史摘要（可能为空）：
{old_summary}

需要压缩的新对话：
{messages}

请输出合并后的新摘要，目标是在丢失最少信息的情况下释放对话上下文空间："""
```

### `object/bot/state.py` — BotState addition

```python
conversation_summary: str  # progressive summary of older messages (dynamic inject)
```

### `bot/core/graph.py` — Graph wiring

```python
# New imports
from bot.core.nodes import summarize_node
from common import BotConfig

# Function signature — add config parameter
async def create_graph(
    llm: ChatOpenAI, config: BotConfig, db_dir: str = "db"
) -> tuple[CompiledStateGraph, AsyncSqliteSaver]:

    builder = StateGraph(BotState)
    # ... existing nodes ...
    builder.add_node("summarize", partial(summarize_node, llm=llm, config=config))

    builder.add_edge(START, "detect_intent")
    builder.add_edge("detect_intent", "router")
    builder.add_conditional_edges(
        "router",
        lambda s: "call_llm" if s.get("should_respond", True) else END,
    )
    builder.add_edge("call_llm", "summarize")   # always run (node handles skip)
    builder.add_edge("summarize", END)
    # ... checkpoint setup ...
```

> **Note**: No conditional edge on `call_llm → summarize` — the `summarize_node`
> checks the token threshold and returns `{}` (no-op) when below threshold.
> A straight edge is simpler and avoids computing token counts twice.
>
> **Also**: `create_graph` now accepts `BotConfig` so it can bind
> `config` to the summarize node via `partial`.

### `main.py` — call site update

```python
# Before:
graph, checkpointer = await create_graph(llm, db_dir=config.db_dir)
# After:
graph, checkpointer = await create_graph(llm, config, db_dir=config.db_dir)
```

### `bot/core/nodes/llm_node/call_llm.py` — SystemMessage injection update

```python
# Build dynamic SystemMessages (never persisted to checkpoint)
system_msgs = [SystemMessage(content=state["persona"])]

memories = state.get("user_memories", "").strip()
if memories:
    system_msgs.append(SystemMessage(content=f"关于当前用户已知的信息：\n{memories}"))

summary = state.get("conversation_summary", "").strip()
if summary:
    system_msgs.append(SystemMessage(content=f"之前的对话摘要：\n{summary}"))

messages = system_msgs + state["messages"]
response = await llm.ainvoke(messages)
```

### `bot/core/nodes/action_node/__init__.py`

```python
from .detect_intent import detect_intent
from .summarize import summarize_node
```

### `bot/core/nodes/__init__.py`

```python
from .action_node import detect_intent, summarize_node
from .llm_node import call_llm_node, router_node

__all__ = ["call_llm_node", "detect_intent", "router_node", "summarize_node"]
```

## Data Flow

```
call_llm completes
  → summarize_node:
      1. estimate_context_tokens(all layers)
      2. if ≤ trigger_threshold → return {} (no-op)
      3. trim_messages(state["messages"], keep=keep_ratio × context_window)
      4. LLM(old_summary + trimmed_messages → new_summary)
      5. return {messages: [RemoveMessage...], conversation_summary: new_summary}
  → END
```

Next invocation:
  → call_llm_node picks up updated `conversation_summary`
  → injects as messages[2]
  → state["messages"] has been trimmed to sliding window

## Verification

1. **Unit**: `estimate_context_tokens` returns plausible values for known message
   sets; `should_summarize` correctly compares against threshold.
2. **Integration**: Run bot with `llm_context_window=4000` (small value), send
   20+ rounds of conversation, verify:
   - Summary is generated after token threshold reached
   - `conversation_summary` appears in checkpoint state
   - Old messages are removed from `messages`
   - LLM can still reference earlier conversation topics via summary
3. **Idempotency**: Repeated invocations with same state don't double-summarize
   (old messages already removed, new token count below threshold).
