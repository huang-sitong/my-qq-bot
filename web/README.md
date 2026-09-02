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

## 目录结构

```
web/
├── index.html          # 入口 HTML
├── vite.config.ts      # Vite 配置
├── tsconfig*.json      # TypeScript 配置
└── src/
    ├── main.ts         # 应用入口（挂载 App）
    ├── App.vue         # 根组件
    ├── assets/         # 静态资源
    ├── components/     # 组件
    └── style.css       # 全局样式
```
