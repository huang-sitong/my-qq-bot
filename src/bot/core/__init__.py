"""Bot core message pipeline.

The heavy workflow/tool/context implementation now lives in
``orchestration``, ``execution`` and ``context`` packages; this package
keeps the message-facing pipeline (ingress/router/dispatcher/worker/llm).
"""

from orchestration.graph import create_graph

from .llm import setup_llm

__all__ = [
    "create_graph",
    "setup_llm",
]
