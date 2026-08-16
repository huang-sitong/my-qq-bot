"""兼容层：指令注册表位于 ``commands.registry``。"""
from commands.registry import CommandRegistry, can_run, run_command

__all__ = ["CommandRegistry", "can_run", "run_command"]
