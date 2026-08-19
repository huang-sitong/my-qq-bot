"""``src.bot.package``：bot 应用包主体。

本包是架构重构后的应用根包，全部上下文统一在此管理：

- ``core``：应用装配与生命周期（app/boot）
- ``pipeline``：协议无关的事件流水线
- ``utils``：纯工具与横切设施
- ``platform``：平台适配层（当前为 Satori）
- ``config``：配置类
- ``tools``：内部工具装配
- ``mcp``：MCP 配置加载与客户端适配
- ``commands`` / ``conversation`` / ``domain`` / ``knowledge`` / ``memory`` /
  ``orchestration`` / ``skill`` / ``vision``：业务限界上下文

为保持导入安全，本文件不重导出任何子包；请从具体模块导入。
"""
