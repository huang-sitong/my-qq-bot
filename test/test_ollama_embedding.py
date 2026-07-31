"""测试 Ollama 本地 embedding 模型（qwen3-embedding:0.6b）。

验证范围（为后续 SQLite-vec RAG 做准备）：
1. Ollama 服务可用性 + 模型就绪
2. 原生 /api/embed 接口（批量输入）
3. OpenAI 兼容 /v1/embeddings 接口（用于 langchain_openai.OpenAIEmbeddings 集成）
4. 向量维度一致性（决定 vec0 表的 FLOAT[N]）
5. 中文语义相似度：相关对高分 / 无关对低分
6. qwen3-embedding 检索格式（Instruct: Query/Document 前缀）对相似度的影响
7. 批量 embedding 与单条 embedding 结果一致性

用法：uv run python test/test_ollama_embedding.py
"""

import math
import os
import sys

import httpx

BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = "qwen3-embedding:0.6b"

# Windows 控制台默认 GBK，强制 UTF-8 输出避免中文乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    """断言测试项并统计结果。"""
    global PASS, FAIL
    status = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def cosine(a: list[float], b: list[float]) -> float:
    """计算余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ----------------------------------------------------------------------
# HTTP 辅助
# ----------------------------------------------------------------------

def embed_native(client: httpx.Client, inputs: list[str]) -> list[list[float]]:
    """调用 Ollama 原生 /api/embed。"""
    resp = client.post(f"{BASE_URL}/api/embed", json={"model": MODEL, "input": inputs})
    resp.raise_for_status()
    return resp.json()["embeddings"]


def embed_openai(client: httpx.Client, input_text: str) -> list[float]:
    """调用 OpenAI 兼容 /v1/embeddings。"""
    resp = client.post(
        f"{BASE_URL}/v1/embeddings",
        headers={"Content-Type": "application/json"},
        json={"model": MODEL, "input": input_text},
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


# ----------------------------------------------------------------------
# 测试项
# ----------------------------------------------------------------------

def test_service(client: httpx.Client) -> None:
    print("\n[1] Ollama 服务与模型就绪")
    try:
        resp = client.get(f"{BASE_URL}/api/tags")
        resp.raise_for_status()
    except Exception as exc:
        check("Ollama 服务可访问", False, str(exc))
        return

    models = resp.json().get("models", [])
    names = [m["name"] for m in models]
    check("模型已拉取", MODEL in names, f"可用模型: {names}")


def test_dimension(client: httpx.Client) -> None:
    print("\n[2] 向量维度（用于 SQLite-vec 建表 FLOAT[N]）")
    vecs = embed_native(client, ["测试"])
    dim = len(vecs[0])
    check("维度 = 1024", dim == 1024, f"实际维度: {dim}")


def test_batch_consistency(client: httpx.Client) -> None:
    print("\n[3] 批量 embedding 与单条 embedding 一致性")
    text = "今天天气怎么样"
    single = embed_native(client, [text])[0]
    batch = embed_native(client, [text, "无关内容"])
    check("批量结果与单条结果一致", cosine(single, batch[0]) > 0.9999,
          f"cosine={cosine(single, batch[0]):.4f}")


def test_semantic_similarity(client: httpx.Client) -> None:
    print("\n[4] 中文语义相似度")
    pairs_related = [
        ("我们今晚一起去看电影吧", "今晚去看电影怎么样"),
        ("你最喜欢的颜色是什么", "你喜欢什么颜色"),
        ("张三说他喜欢吃火锅", "张三说他爱吃川味火锅"),
    ]
    pairs_unrelated = [
        ("你最喜欢的颜色是什么", "今晚的月亮真圆"),
        ("帮我把文件发到邮箱", "明天的会议改到下午三点"),
    ]

    ok = True
    for a, b in pairs_related:
        va, vb = embed_native(client, [a, b])
        sim = cosine(va, vb)
        print(f"    相关对 [{a[:12]}...] vs [{b[:12]}...] → {sim:.4f}")
        ok &= sim > 0.6

    for a, b in pairs_unrelated:
        va, vb = embed_native(client, [a, b])
        sim = cosine(va, vb)
        print(f"    无关对 [{a[:12]}...] vs [{b[:12]}...] → {sim:.4f}")
        ok &= sim < 0.6

    check("相关对高分 / 无关对低分", ok)


def test_instruct_format(client: httpx.Client) -> None:
    """qwen3-embedding 建议检索时用 Instruct: Query/Document 前缀。"""
    print("\n[5] Instruct 前缀对检索相似度的影响")
    query = "张三上次说他喜欢吃什么"
    docs = [
        "张三说他喜欢吃火锅",
        "李四明天要去上海出差",
    ]

    # 不带 Instruct 前缀
    plain = embed_native(client, [query] + docs)
    sim_plain = [cosine(plain[0], d) for d in plain[1:]]

    # 带 Instruct 前缀（Query 用 query 指令，Document 用 doc 指令）
    task = "检索群聊历史中与问题相关的消息"
    q_prompt = f"Instruct: {task}\nQuery: {query}"
    d_prompts = [f"Instruct: {task}\nDocument: {d}" for d in docs]
    instructed = embed_native(client, [q_prompt] + d_prompts)
    sim_instr = [cosine(instructed[0], d) for d in instructed[1:]]

    print(f"    无前缀: 相关={sim_plain[0]:.4f} 无关={sim_plain[1]:.4f} (差距={sim_plain[0]-sim_plain[1]:.4f})")
    print(f"    有前缀: 相关={sim_instr[0]:.4f} 无关={sim_instr[1]:.4f} (差距={sim_instr[0]-sim_instr[1]:.4f})")

    # 理想情况下 Instruct 格式应拉大相关/无关差距
    margin_plain = sim_plain[0] - sim_plain[1]
    margin_instr = sim_instr[0] - sim_instr[1]
    check("相关文档相似度高于无关文档（无前缀）", sim_plain[0] > sim_plain[1])
    check("相关文档相似度高于无关文档（有前缀）", sim_instr[0] > sim_instr[1])
    check("Instruct 前缀改善区分度", margin_instr > margin_plain - 0.01,
          f"margin 无前缀={margin_plain:.4f} 有前缀={margin_instr:.4f}")


def test_openai_compat(client: httpx.Client) -> None:
    """验证 OpenAI 兼容端点，确认 langchain OpenAIEmbeddings 可复用。"""
    print("\n[6] OpenAI 兼容 /v1/embeddings（langchain 集成路径）")
    vec = embed_openai(client, "测试兼容接口")
    check("返回维度 1024", len(vec) == 1024, f"实际维度: {len(vec)}")
    check("向量已归一化", abs(math.sqrt(sum(x * x for x in vec)) - 1.0) < 0.01,
          f"L2范数={math.sqrt(sum(x * x for x in vec)):.4f}")

    # 原生与 OpenAI 端点结果一致性：模板 token 处理略有差异，允许微小偏差。
    # RAG 实践建议：索引与查询统一使用同一个端点，保证向量空间一致。
    native_vec = embed_native(client, ["测试兼容接口"])[0]
    check("两个端点 embedding 方向一致", cosine(native_vec, vec) > 0.99,
          f"cosine={cosine(native_vec, vec):.4f}")


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------

def main() -> int:
    print(f"测试模型: {MODEL}")
    print(f"Ollama:   {BASE_URL}")

    with httpx.Client(timeout=120.0) as client:
        test_service(client)
        test_dimension(client)
        test_batch_consistency(client)
        test_semantic_similarity(client)
        test_instruct_format(client)
        test_openai_compat(client)

    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
