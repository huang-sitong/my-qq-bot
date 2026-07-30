# Context Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add progressive summarization with a sliding window to keep LLM context within model limits.

**Architecture:** New `summarize` action_node runs after `call_llm`, using LangChain's `count_tokens_approximately` + `trim_messages` utilities. When token count exceeds `trigger_ratio × context_window`, older messages are compressed into `conversation_summary` (a new BotState field) and removed from `state["messages"]`. The summary is dynamically injected as a separate `SystemMessage` in `call_llm_node`, alongside persona and user memories — none of these enter the persisted checkpoint.

**Tech Stack:** Python 3.12+, LangChain `count_tokens_approximately` / `trim_messages` / `RemoveMessage`, LangGraph `StateGraph` + `AsyncSqliteSaver`

## Global Constraints

- SystemMessages (persona, memories, summary) are **never** persisted in checkpoint — they are dynamically injected each `call_llm` invocation
- Summary uses the **same LLM model** (no separate summarization model)
- All new config fields use **plain defaults** (no env-var lookup) matching pattern of `ws_url`, `token`, etc.
- Existing graph topology (detect_intent → router → call_llm) is preserved; summarize is appended after call_llm
- `chars_per_token=1.5` for Chinese token estimation

---

### Task 1: Add context window config to BotConfig

**Files:**
- Modify: `common/config.py`

**Interfaces:**
- Produces: `BotConfig.llm_context_window: int`, `BotConfig.summary_trigger_ratio: float`, `BotConfig.summary_keep_ratio: float`, `BotConfig.summary_max_input_tokens: int`

- [ ] **Step 1: Add four context fields to BotConfig**

In `common/config.py`, add a new `# --- Context Window ---` section after the `persona_prompt` field and before `# --- LLM ---`, with four plain-default fields:

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

- [ ] **Step 2: Verify the file is valid Python**

```bash
uv run python -c "from common import BotConfig; c = BotConfig(); print(f'context_window={c.llm_context_window}, trigger={c.summary_trigger_ratio}, keep={c.summary_keep_ratio}, max_input={c.summary_max_input_tokens}')"
```

Expected: `context_window=200000, trigger=0.6, keep=0.2, max_input=8000`

- [ ] **Step 3: Commit**

```bash
git add common/config.py
git commit -m "feat: add context window config fields to BotConfig"
```

---

### Task 2: Add SUMMARY_PROMPT and export it

**Files:**
- Modify: `common/prompts.py`
- Modify: `common/__init__.py`

**Interfaces:**
- Produces: `common.prompts.SUMMARY_PROMPT: str`, exported via `common.__init__`

- [ ] **Step 1: Add SUMMARY_PROMPT to prompts.py**

In `common/prompts.py`, append the `SUMMARY_PROMPT` constant after the existing `EXTRACT_PROMPT`:

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

- [ ] **Step 2: Export SUMMARY_PROMPT from common/__init__.py**

In `common/__init__.py`, update the imports and `__all__`:

```python
from .prompts import DEFAULT_PERSONA_PROMPT, EXTRACT_PROMPT, ROUTER_PROMPT, SUMMARY_PROMPT

__all__ = [
    "BotConfig",
    "DEFAULT_PERSONA_PROMPT",
    "EXTRACT_PROMPT",
    "ROUTER_PROMPT",
    "SUMMARY_PROMPT",
]
```

- [ ] **Step 3: Verify**

```bash
uv run python -c "from common import SUMMARY_PROMPT; print('SUMMARY_PROMPT length:', len(SUMMARY_PROMPT)); print('has old_summary placeholder:', '{old_summary}' in SUMMARY_PROMPT); print('has messages placeholder:', '{messages}' in SUMMARY_PROMPT)"
```

Expected: `SUMMARY_PROMPT length: <some number>`, `has old_summary placeholder: True`, `has messages placeholder: True`

- [ ] **Step 4: Commit**

```bash
git add common/prompts.py common/__init__.py
git commit -m "feat: add SUMMARY_PROMPT for conversation summarization"
```

---

### Task 3: Add conversation_summary to BotState

**Files:**
- Modify: `object/bot/state.py`

**Interfaces:**
- Produces: `BotState.conversation_summary: str`

- [ ] **Step 1: Add the field**

In `object/bot/state.py`, add `conversation_summary` after `user_memories` and before `session_id`:

```python
class BotState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    persona: str
    user_memories: str
    conversation_summary: str   # progressive summary of older messages (dynamic inject)
    session_id: str
    # ... rest unchanged ...
```

- [ ] **Step 2: Verify the TypedDict is valid**

```bash
uv run python -c "from object.bot.state import BotState; print([k for k in BotState.__annotations__]); assert 'conversation_summary' in BotState.__annotations__, 'Missing field'"
```

Expected: prints all keys including `conversation_summary`, no assertion error

- [ ] **Step 3: Commit**

```bash
git add object/bot/state.py
git commit -m "feat: add conversation_summary field to BotState"
```

---

### Task 4: Create bot/core/context.py — token estimation utilities

**Files:**
- Create: `bot/core/context.py`

**Interfaces:**
- Produces: `estimate_context_tokens(messages, persona, memories, summary) -> int`, `format_messages_for_summary(messages) -> str`

- [ ] **Step 1: Create the module**

Create `bot/core/context.py`:

```python
"""Context window utilities for token estimation and message formatting.

Wraps LangChain built-in ``count_tokens_approximately`` and
``trim_messages`` for the QQ bot's three-layer context structure.
"""

import logging

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately

logger = logging.getLogger(__name__)

# Chinese text averages ~1.5 characters per token (vs ~4 for English)
_CHARS_PER_TOKEN = 1.5


def estimate_context_tokens(
    messages: list[BaseMessage],
    persona: str,
    memories: str,
    summary: str,
) -> int:
    """Estimate total tokens for the full context sent to the LLM.

    Builds the same three-layer structure that ``call_llm_node`` uses
    and passes it through ``count_tokens_approximately`` for a single
    consistent token count.
    """
    all_msgs: list[BaseMessage] = []

    # Layer 0: persona (always present)
    if persona.strip():
        all_msgs.append(SystemMessage(content=persona))

    # Layer 1: user memories (optional)
    if memories.strip():
        all_msgs.append(SystemMessage(
            content=f"关于当前用户已知的信息：\n{memories}"
        ))

    # Layer 2: conversation summary (optional)
    if summary.strip():
        all_msgs.append(SystemMessage(
            content=f"之前的对话摘要：\n{summary}"
        ))

    # Layer 3..N: recent messages
    all_msgs.extend(messages)

    return count_tokens_approximately(
        all_msgs,
        chars_per_token=_CHARS_PER_TOKEN,
    )


def format_messages_for_summary(messages: list[BaseMessage]) -> str:
    """Convert a list of messages to a readable text block for summarization.

    Each message is formatted as ``[Role | name]: content`` or
    ``[Role]: content``, one per line.
    """
    lines: list[str] = []
    for m in messages:
        role = type(m).__name__.replace("Message", "")
        content = getattr(m, "content", str(m))
        name = getattr(m, "name", "") or ""
        if name:
            lines.append(f"[{role} | {name}]: {content}")
        else:
            lines.append(f"[{role}]: {content}")
    return "\n".join(lines)
```

- [ ] **Step 2: Verify the module imports and basic functionality**

```bash
uv run python -c "
from langchain_core.messages import HumanMessage, AIMessage
from bot.core.context import estimate_context_tokens, format_messages_for_summary

# Test format_messages_for_summary
msgs = [
    HumanMessage(content='你好', name='user1'),
    AIMessage(content='你好！有什么可以帮你的？'),
]
formatted = format_messages_for_summary(msgs)
print('Formatted:')
print(formatted)
assert '[Human | user1]: 你好' in formatted
assert '[AI]: 你好！有什么可以帮你的？' in formatted

# Test estimate_context_tokens returns a positive int
tokens = estimate_context_tokens(msgs, 'You are a helpful assistant.', '', '')
print(f'Tokens: {tokens}')
assert tokens > 0, f'Expected positive token count, got {tokens}'
print('All checks passed')
"
```

- [ ] **Step 3: Commit**

```bash
git add bot/core/context.py
git commit -m "feat: add context.py with token estimation and message formatting utilities"
```

---

### Task 5: Create summarize action_node

**Files:**
- Create: `bot/core/nodes/action_node/summarize.py`
- Modify: `bot/core/nodes/action_node/__init__.py`

**Interfaces:**
- Consumes: `BotState`, `ChatOpenAI`, `BotConfig`, `SUMMARY_PROMPT` (from `common`), `estimate_context_tokens`, `format_messages_for_summary` (from `bot.core.context`), `trim_messages` (from `langchain_core.messages`), `RemoveMessage` (from `langchain_core.messages`)
- Produces: `summarize_node(state, llm, config) -> dict`

- [ ] **Step 1: Create summarize.py**

Create `bot/core/nodes/action_node/summarize.py`:

```python
"""summarize — compress older conversation messages into a progressive summary.

Runs after ``call_llm_node`` on every invocation. Checks whether the total
context exceeds the configured trigger threshold; if so, trims old messages
and generates a merged summary via LLM.
"""

import logging

from langchain_core.messages import (
    HumanMessage,
    RemoveMessage,
    trim_messages,
)
from langchain_openai import ChatOpenAI

from bot.core.context import estimate_context_tokens, format_messages_for_summary
from common import BotConfig, SUMMARY_PROMPT
from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def summarize_node(
    state: BotState,
    llm: ChatOpenAI,
    config: BotConfig,
) -> dict:
    """Check context size; if over threshold, summarize old messages.

    Returns ``{}`` (no-op) when below threshold or when there are no
    messages to compress.  Otherwise returns ``RemoveMessage`` updates
    and a new ``conversation_summary``.
    """
    trigger = int(config.summary_trigger_ratio * config.llm_context_window)

    # 1. Check if summarization is needed
    total = estimate_context_tokens(
        state["messages"],
        state.get("persona", ""),
        state.get("user_memories", ""),
        state.get("conversation_summary", ""),
    )
    logger.debug(
        "summarize check: total=%d trigger=%d session=%s",
        total, trigger, state.get("session_id", ""),
    )

    if total <= trigger:
        return {}  # No summarization needed

    # 2. Split messages: keep recent, summarize the rest
    keep_tokens = int(config.summary_keep_ratio * config.llm_context_window)
    keep_messages = trim_messages(
        state["messages"],
        max_tokens=keep_tokens,
        token_counter="approximate",
        strategy="last",
        start_on="human",
    )
    keep_ids = {m.id for m in keep_messages if getattr(m, "id", None)}
    to_summarize = [m for m in state["messages"] if m.id not in keep_ids]

    if not to_summarize:
        return {}

    logger.info(
        "Summarizing %d messages (keeping %d) for session %s",
        len(to_summarize), len(keep_messages), state.get("session_id", ""),
    )

    # 3. Generate summary via LLM
    old_summary = state.get("conversation_summary", "")
    formatted_messages = format_messages_for_summary(to_summarize)

    # Truncate input to summarization LLM if needed
    if config.summary_max_input_tokens > 0:
        trimmed_input = trim_messages(
            [HumanMessage(content=formatted_messages)],
            max_tokens=config.summary_max_input_tokens,
            token_counter="approximate",
            strategy="last",
        )
        formatted_messages = (
            trimmed_input[0].content if trimmed_input else formatted_messages
        )

    summary_prompt = SUMMARY_PROMPT.format(
        old_summary=old_summary or "（无）",
        messages=formatted_messages,
    )

    try:
        response = await llm.ainvoke([HumanMessage(content=summary_prompt)])
        new_summary = response.content if hasattr(response, "content") else str(response)
    except Exception:
        logger.exception("Summary generation failed for session %s", state.get("session_id"))
        return {}  # Non-critical — skip summarization on failure

    # 4. Remove summarized messages from state
    removes = [RemoveMessage(id=m.id) for m in to_summarize]

    logger.info(
        "Summary generated: %d chars, removed %d messages for session %s",
        len(new_summary), len(to_summarize), state.get("session_id"),
    )
    return {
        "messages": removes,
        "conversation_summary": new_summary,
    }
```

- [ ] **Step 2: Update action_node/__init__.py**

In `bot/core/nodes/action_node/__init__.py`, add the `summarize_node` export:

```python
from .detect_intent import detect_intent
from .summarize import summarize_node

__all__ = ["detect_intent", "summarize_node"]
```

- [ ] **Step 3: Verify the module imports correctly**

```bash
uv run python -c "
from bot.core.nodes.action_node import summarize_node
import inspect
print('summarize_node is async:', inspect.iscoroutinefunction(summarize_node))
sig = inspect.signature(summarize_node)
print('Parameters:', list(sig.parameters.keys()))
assert 'state' in sig.parameters
assert 'llm' in sig.parameters
assert 'config' in sig.parameters
print('All checks passed')
"
```

Expected: `summarize_node is async: True`, `Parameters: ['state', 'llm', 'config']`

- [ ] **Step 4: Commit**

```bash
git add bot/core/nodes/action_node/summarize.py bot/core/nodes/action_node/__init__.py
git commit -m "feat: add summarize action_node for progressive context summarization"
```

---

### Task 6: Update call_llm_node to inject conversation_summary

**Files:**
- Modify: `bot/core/nodes/llm_node/call_llm.py`

**Interfaces:**
- Consumes: `BotState.conversation_summary`
- Produces: (unchanged interface — still returns `dict` with `messages` and `reply_text`)

- [ ] **Step 1: Refactor SystemMessage injection to three-layer structure**

Replace the current `call_llm_node` implementation in `bot/core/nodes/llm_node/call_llm.py`:

```python
import logging

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

from object.bot.state import BotState

logger = logging.getLogger(__name__)


async def call_llm_node(state: BotState, llm: ChatOpenAI) -> dict:
    """Call the LLM with dynamically injected persona, memories, and summary.

    SystemMessages are built fresh each invocation and never persisted
    to checkpoint, so the persona is always at messages[0] regardless of
    conversation length.
    """
    # Build dynamic SystemMessages (never persisted to checkpoint)
    system_msgs = [SystemMessage(content=state["persona"])]

    # Layer 1: user memories (optional)
    memories = state.get("user_memories", "").strip()
    if memories:
        system_msgs.append(SystemMessage(
            content=f"关于当前用户已知的信息：\n{memories}"
        ))

    # Layer 2: conversation summary (optional)
    summary = state.get("conversation_summary", "").strip()
    if summary:
        system_msgs.append(SystemMessage(
            content=f"之前的对话摘要：\n{summary}"
        ))

    # Layer 3..N: recent messages
    messages = system_msgs + state["messages"]

    try:
        response = await llm.ainvoke(messages)
        reply = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        if isinstance(exc, type(TimeoutError(""))) or "Timeout" in type(exc).__name__:
            logger.warning("LLM call timed out for session %s", state["session_id"])
        else:
            logger.exception("LLM call failed for session %s", state["session_id"])
        reply = "我暂时无法思考，请稍后再试"

    return {"messages": [AIMessage(content=reply)], "reply_text": reply}
```

- [ ] **Step 2: Verify the refactored node**

```bash
uv run python -c "
from bot.core.nodes.llm_node.call_llm import call_llm_node
import inspect
# Verify it's still async with same signature
assert inspect.iscoroutinefunction(call_llm_node)
sig = inspect.signature(call_llm_node)
print('Parameters:', list(sig.parameters.keys()))
print('OK - call_llm_node signature unchanged')
"
```

- [ ] **Step 3: Commit**

```bash
git add bot/core/nodes/llm_node/call_llm.py
git commit -m "refactor: inject conversation_summary as separate SystemMessage in call_llm"
```

---

### Task 7: Wire summarize into graph, update exports, and update main.py

**Files:**
- Modify: `bot/core/nodes/__init__.py`
- Modify: `bot/core/graph.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: `summarize_node` (from Task 5), `BotConfig` (from `common`)

- [ ] **Step 1: Export summarize_node from nodes/__init__.py**

Update `bot/core/nodes/__init__.py`:

```python
from .action_node import detect_intent, summarize_node
from .llm_node import call_llm_node, router_node

__all__ = ["call_llm_node", "detect_intent", "router_node", "summarize_node"]
```

- [ ] **Step 2: Update create_graph signature and wire summarize node**

In `bot/core/graph.py`, make the following edits:

**Edit A:** Add `summarize_node` to imports and add `BotConfig` import:

```python
from bot.core.nodes import call_llm_node, detect_intent, router_node, summarize_node
from common import BotConfig
```

**Edit B:** Change function signature to accept `config`:

```python
async def create_graph(
    llm: ChatOpenAI, config: BotConfig, db_dir: str = "db"
) -> tuple[CompiledStateGraph, AsyncSqliteSaver]:
```

**Edit C:** Add the summarize node (after the `call_llm` node):

```python
    builder.add_node("summarize", partial(summarize_node, llm=llm, config=config))
```

**Edit D:** Change the edge from `call_llm` — it now goes to `summarize` instead of `END`:

```python
    builder.add_edge("call_llm", "summarize")   # always run (node handles skip)
    builder.add_edge("summarize", END)
```

Remove the old `builder.add_edge("call_llm", END)` line.

- [ ] **Step 3: Update main.py call site**

In `main.py`, change:

```python
graph, checkpointer = await create_graph(llm, db_dir=config.db_dir)
```

To:

```python
graph, checkpointer = await create_graph(llm, config, db_dir=config.db_dir)
```

- [ ] **Step 4: Verify the full import chain and graph compilation**

```bash
uv run python -c "
import asyncio
from common import BotConfig
from bot.core.llm import setup_llm

async def test():
    config = BotConfig()
    llm = setup_llm(model='test', temperature=0)
    from bot.core.graph import create_graph
    graph, checkpointer = await create_graph(llm, config, db_dir='db')
    print('Graph nodes:', list(graph.nodes.keys()))
    assert 'summarize' in graph.nodes, 'summarize node missing from graph'
    print('Graph compiled successfully')
    await checkpointer.aclose()

asyncio.run(test())
"
```

Expected: `Graph nodes: ['detect_intent', 'router', 'call_llm', 'summarize']` and `Graph compiled successfully`

- [ ] **Step 5: Commit**

```bash
git add bot/core/nodes/__init__.py bot/core/graph.py main.py
git commit -m "feat: wire summarize node into graph, update create_graph signature"
```

---

## Verification (end-to-end)

After all tasks are complete, run a manual integration check:

```bash
uv run python -c "
import asyncio
from langchain_core.messages import HumanMessage, AIMessage
from bot.core.context import estimate_context_tokens

async def verify():
    # 1. Verify token estimation scales with messages
    empty = estimate_context_tokens([], 'You are a bot.', '', '')
    print(f'Baseline tokens (persona only): {empty}')

    msgs = [HumanMessage(content='你好' * 50), AIMessage(content='你好！' * 20)]
    with_conversation = estimate_context_tokens(msgs, 'You are a bot.', '', '')
    print(f'Tokens with 2 messages: {with_conversation}')
    assert with_conversation > empty, 'Token count should grow with messages'

    # 2. Verify summary prompt formatting
    from bot.core.context import format_messages_for_summary
    formatted = format_messages_for_summary(msgs)
    assert '[Human]' in formatted or '[Human ' in formatted
    assert '[AI]' in formatted or '[AI ' in formatted
    print('format_messages_for_summary: OK')

    # 3. Verify BotConfig defaults
    from common import BotConfig
    c = BotConfig()
    assert c.llm_context_window == 200_000
    assert c.summary_trigger_ratio == 0.6
    assert c.summary_keep_ratio == 0.2
    assert c.summary_max_input_tokens == 8_000
    print('BotConfig defaults: OK')

    # 4. Verify BotState has new field
    from object.bot.state import BotState
    assert 'conversation_summary' in BotState.__annotations__
    print('BotState.conversation_summary: OK')

    # 5. Verify graph compiles with summarize node
    from bot.core.llm import setup_llm
    from bot.core.graph import create_graph
    llm = setup_llm(model='test', temperature=0)
    graph, checkpointer = await create_graph(llm, c, db_dir='db')
    nodes = list(graph.nodes.keys())
    assert 'summarize' in nodes, f'summarize missing from {nodes}'
    assert 'detect_intent' in nodes
    assert 'router' in nodes
    assert 'call_llm' in nodes
    print(f'Graph nodes: {nodes}: OK')
    await checkpointer.aclose()

    print('All verification checks passed!')

asyncio.run(verify())
"
```

Expected: all checks pass, no assertion errors.
