# qq-bot-console

基于 Vue 3 + Vite + TypeScript + Element Plus 的 QQ 机器人控制台前端。

## 技术栈

- [Vue 3](https://vuejs.org/) — 渐进式框架
- [Vite](https://vite.dev/) — 构建工具（开发服务器 + 生产构建）
- [TypeScript](https://www.typescriptlang.org/) — 类型系统
- [Element Plus](https://element-plus.org/) — 组件库
- [Vue Router](https://router.vuejs.org/) — 路由
- [Pinia](https://pinia.vuejs.org/) — 状态管理
- [Axios](https://axios-http.com/) — HTTP 请求

## 常用命令

```bash
npm install     # 安装依赖
npm run dev     # 启动开发服务器
npm run build   # 类型检查 + 生产构建（输出到 dist/）
npm run preview # 本地预览生产构建
```

## 页面

- `/settings` — 设置中心：读取/编辑后端配置（对应 `GET/PUT /api/settings`）
  - 按分组（传输 / LLM / RAG 等）侧边栏切换
  - 布尔用开关、数字用步进器、列表用逗号分隔文本、敏感字段用掩码密码框
  - 只提交发生修改的字段；敏感字段留空视为不修改
  - 「恢复默认」发送 `null` 清除该项，由后端恢复默认值

## 开发联调

`vite.config.ts` 已配置 dev proxy：`/api` 代理到 `http://127.0.0.1:8000`（FastAPI 控制台后端），开发时直接 `npm run dev` 即可，无需额外配置跨域。

```bash
# 终端 1：启动后端
uv run python console_api.py

# 终端 2：启动前端
cd web && npm run dev
```

## 目录结构

```
web/
├── index.html              # 入口 HTML
├── vite.config.ts          # Vite 配置（含 /api dev proxy）
├── tsconfig*.json          # TypeScript 配置
└── src/
    ├── main.ts             # 应用入口（挂载 Element Plus / Pinia / Router）
    ├── App.vue             # 根组件（router-view）
    ├── api/settings.ts     # 设置 API 客户端
    ├── types/settings.ts   # 设置 API 类型
    ├── stores/settings.ts  # 设置 Pinia store（加载/草稿/脏标记/保存）
    ├── router/index.ts     # 路由
    ├── views/
    │   └── SettingsPage.vue  # 设置中心页面
    ├── components/settings/  # 设置表单组件（SettingField / SecretInput / SettingsForm）
    ├── assets/             # 静态资源
    └── style.css           # 全局样式
```
