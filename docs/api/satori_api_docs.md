# Satori Protocol API 文档

> 来源：https://satori.chat/zh-CN/protocol/api.html 及各资源页面
>
> Satori 是一套通用即时通讯协议，定义了一套基于 HTTP 的 API 服务，用于发送消息和调用其他功能。

## 服务信息

| 项目 | 值 |
|------|-----|
| 服务地址 | `http://127.0.0.1:5600` |
| API 基础路径 | `http://127.0.0.1:5600/v1/{resource}.{method}` |
| 请求方式 | POST |
| 编码格式 | `application/json` |
| 版本 | v1 |

### 必需请求头

| 请求头 | 说明 |
|--------|------|
| `Content-Type` | `application/json` |
| `Satori-Platform` | qq |
| `Satori-User-ID` | 1806933648 |

### 当前状态

服务运行中，已成功连接 QQ 适配器（llonebot）。机器人名称：**RosmontisBot**，状态：**ONLINE**。

所有 28 个已声明特性接口均可用，详见下方接口可用性表。

### 端口

| 协议 | 地址 |
|------|------|
| Satori WS/API | `http://127.0.0.1:5600` |
| OneBot11 HTTP（`send_file` 普通文件上传） | `http://127.0.0.1:3000` |
| OneBot11 正向 WS | `ws://127.0.0.1:3001`（本项目当前未使用） |

### 文件发送能力（LLBot）

- Satori 标准 `upload.create` 资源上传是实验性 API；LLBot 当前源码没有注册
  `upload.create`，且 `message.create` 对 `<file>` 元素只 fetch、不真正发送（上游 TODO）。
- 本项目已用 LLBot 的 OneBot11 HTTP 兜底：
  - 私聊文件：`POST /upload_private_file`
    `{"user_id": "...", "file": "绝对路径", "name": "文件名"}`
  - 群文件：`POST /upload_group_file`
    `{"group_id": "...", "file": "绝对路径", "name": "文件名"}`
- 图片仍走标准 Satori：`message.create` 内容为
  `<img src="file:///本地路径"/>`，LLBot 会把本地图片发送为 QQ 图片消息。
- `SatoriApiClient.send_file()` 已封装上述选择逻辑；agent 侧对应工具是 `send_file`，
  OneBot11 地址由 `BOT_ONEBOT11_API_BASE_URL` 配置。

### 运行信息

| 项目 | 值 |
|------|-----|
| 平台 | llonebot (QQ) |
| 机器人 | RosmontisBot (ID: 1806933648) |
| 机器人头像 | `http://q.qlogo.cn/headimg_dl?dst_uin=1806933648&spec=640` |
| 群组 | RosmontisHome (ID: 796219047) |
| 机器人状态 | ONLINE |
| 支持 API 数 | 28 |

### 接口可用性

| API | 状态 | 备注 |
|-----|:----:|------|
| `login.get` | ✅ | |
| `user.get` | ✅ | |
| `user.channel.create` | ✅ | |
| `guild.get` | ✅ | |
| `guild.list` | ✅ | |
| `guild.approve` | ✅ | |
| `guild.member.get` | ✅ | |
| `guild.member.list` | ✅ | |
| `guild.member.kick` | ✅ | 需要有效 user_id |
| `guild.member.mute` | ✅ | |
| `guild.member.approve` | ✅ | |
| `guild.member.role.set` | ✅ | |
| `guild.role.list` | ✅ | |
| `channel.get` | ✅ | |
| `channel.list` | ✅ | |
| `channel.update` | ✅ | |
| `channel.delete` | ✅ | |
| `channel.mute` | ✅ | |
| `message.create` | ✅ | 发送成功但 echo 超时（适配器配置问题） |
| `message.get` | ✅ | |
| `message.delete` | ✅ | |
| `message.list` | ✅ | |
| `reaction.create` | ✅ | |
| `reaction.delete` | ✅ | |
| `reaction.list` | ✅ | |
| `friend.list` | ✅ | |
| `friend.delete` | ✅ | 需要有效 user_id |
| `friend.approve` | ✅ | |

未在特性列表中（平台不支持）：
| API | 状态 |
|-----|:----:|
| `channel.create` | ❌ method not found |
| `message.update` | ❌ method not found |

---

## 目录

- [协议基础](#协议基础)
  - [HTTP API 格式](#http-api-格式)
  - [鉴权](#鉴权)
  - [状态码](#状态码)
  - [平台特性（实验性）](#平台特性实验性)
  - [进阶 API](#进阶-api)
- [类型定义](#类型定义)
  - [分页列表](#分页列表)
  - [双向分页列表](#双向分页列表)
- [用户 (User)](#用户-user)
- [频道 (Channel)](#频道-channel)
- [消息 (Message)](#消息-message)
- [群组 (Guild)](#群组-guild)
- [群组成员 (GuildMember)](#群组成员-guildmember)
- [群组角色 (GuildRole)](#群组角色-guildrole)
- [好友 (Friend)](#好友-friend)
- [表态 (Reaction)](#表态-reaction)
- [登录信息 (Login)](#登录信息-login)
- [表情 (Emoji)](#表情-emoji-实验性)
- [交互 (Interaction)](#交互-interaction-实验性)
- [事件列表](#事件列表)

---

## 协议基础

### HTTP API 格式

所有 URL 的形式均为 `/{version}/{resource}.{method}`：

| 部分 | 说明 |
|------|------|
| `version` | API 版本号，目前仅有 `v1` |
| `resource` | 资源类型（如 channel、message、user） |
| `method` | 方法名（如 get、create、delete） |

**请求方式：** 绝大多数 API 使用 `POST` 方法，参数通过 `application/json` 编码在请求体中。文件上传 API 使用 `multipart/form-data` 编码。

**请求头：**

| 请求头 | 说明 | 必需 |
|--------|------|:----:|
| `Authorization: Bearer <token>` | 鉴权令牌 | 取决于 SDK 配置 |
| `Satori-Platform` | 平台名称（如 discord、qq） | ✓ |
| `Satori-User-ID` | 平台账号 ID | ✓ |

**请求示例：**

```text
POST /v1/channel.get
Content-Type: application/json
Authorization: Bearer 1234567890
Satori-Platform: discord
Satori-User-ID: 1234567890

{"channel_id": "1234567890"}
```

### 鉴权

鉴权通过 HTTP API 中的 `Authorization` 请求头实现。鉴权令牌由 SDK 分发，本协议不做任何限制。如果 SDK 没有配置鉴权，则应用无需提供上述请求头。

### 状态码

| 状态码 | 描述 |
|--------|------|
| 200 (OK) | 请求成功 |
| 400 (BAD REQUEST) | 请求格式错误 |
| 401 (UNAUTHORIZED) | 缺失鉴权 |
| 403 (FORBIDDEN) | 权限不足 |
| 404 (NOT FOUND) | API 不存在 |
| 405 (METHOD NOT ALLOWED) | 请求方法不支持 |
| 5XX (SERVER ERROR) | 服务器错误 |

> **注意：** 标准 API 不被平台支持时返回 404，只有 API 被平台支持但未被适配器实现时才返回 501。

### 平台特性（实验性）

`Login` 对象中的 `features` 字段是字符串数组，表示平台特性。可以用于判断平台是否支持某些 API。

示例：
- `message.delete` —— 支持撤回消息
- `message.list.from` —— 使用 `message.list` 查询时支持消息 ID 作为分页令牌
- `guild.plain` —— 群组内只能存在一个消息频道

### 进阶 API

| 路由 | 说明 |
|------|------|
| `/{version}/proxy` | 代理平台资源 |
| `/{version}/meta` | SDK 元信息 API |
| `/{version}/internal` | 平台内部 API |

---

## 类型定义

### 分页列表

部分 API 返回分页数据，响应为 `List` 对象：

| 字段 | 类型 | 描述 |
|------|------|------|
| `data` | array | 数据 |
| `next` | string? | 下一页的令牌，为空表示无更多数据 |

### 双向分页列表

极少数 API 返回可双向延伸的分页数据，响应为 `BidiList` 对象：

| 字段 | 类型 | 描述 |
|------|------|------|
| `data` | array | 数据 |
| `prev` | string? | 上一页的令牌 |
| `next` | string? | 下一页的令牌 |

**`direction` 参数取值：**

| 值 | 说明 |
|----|------|
| `before` | 向前获取，此时 `prev` 和 `next` 相同，均表示上一页令牌 |
| `after` | 向后获取，此时 `prev` 和 `next` 相同，均表示下一页令牌 |
| `around` | 向两侧获取，`prev` 表示上一页令牌，`next` 表示下一页令牌 |

`prev` 或 `next` 缺失表示在该方向上无更多数据。

**`order` 参数取值：**

| 值 | 说明 |
|----|------|
| `asc` | 升序排列 |
| `desc` | 降序排列 |

---

## 用户 (User)

### 类型定义

| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | 用户 ID |
| `name` | string? | 用户名称 |
| `nick` | string? | 用户昵称（应用层优先使用，为空时回退到 `name`） |
| `avatar` | string? | 用户头像链接 |
| `is_bot` | boolean? | 是否为机器人 |

### API

#### 获取用户信息

```
POST /user.get
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `user_id` | string | 用户 ID |

**响应：** `User` 对象。

---

## 频道 (Channel)

### 类型定义

#### Channel

| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | 频道 ID |
| `type` | ChannelType | 频道类型 |
| `name` | string? | 频道名称 |
| `parent_id` | string? | 父频道 ID |

#### ChannelType 枚举

| 名称 | 值 | 描述 |
|------|:---:|------|
| `TEXT` | 0 | 文本频道 |
| `DIRECT` | 1 | 私聊频道 |
| `CATEGORY` | 2 | 分类频道 |
| `VOICE` | 3 | 语音频道 |

### API

#### 获取频道

```
POST /channel.get
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `channel_id` | string | 频道 ID |

**响应：** `Channel` 对象。

#### 获取频道列表

```
POST /channel.list
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `guild_id` | string | 群组 ID |
| `next` | string? | 分页令牌 |

**响应：** `Channel` 的分页列表。

#### 创建频道

```
POST /channel.create
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `guild_id` | string | 群组 ID |
| `data` | Channel | 频道数据 |

**响应：** `Channel` 对象。

#### 修改频道

```
POST /channel.update
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `channel_id` | string | 频道 ID |
| `data` | Channel | 频道数据 |

#### 删除频道

```
POST /channel.delete
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `channel_id` | string | 频道 ID |

#### 禁言频道（实验性）

```
POST /channel.mute
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `channel_id` | string | 频道 ID |
| `duration` | number | 禁言时长（毫秒），`0` 表示解除禁言 |

#### 创建私聊频道

```
POST /user.channel.create
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `user_id` | string | 用户 ID |
| `guild_id` | string? | 群组 ID |

**响应：** `Channel` 对象。

### 事件

| 事件名 | 描述 |
|--------|------|
| `channel-added` | 频道被创建或变得对 SDK 可见时触发 |
| `channel-updated` | 频道信息更新时触发 |
| `channel-removed` | 频道被删除或变得对 SDK 不可见时触发 |

---

## 消息 (Message)

### 类型定义

| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | 消息 ID |
| `content` | string? | 消息内容 |
| `channel` | Channel? | 频道对象 |
| `guild` | Guild? | 群组对象 |
| `member` | GuildMember? | 群组成员对象 |
| `user` | User? | 用户对象 |
| `created_at` | number? | 消息发送时间戳 |
| `updated_at` | number? | 消息修改时间戳 |

### API

#### 发送消息

```
POST /message.create
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `channel_id` | string | 频道 ID |
| `content` | string | 消息内容 |

**响应：** `Message[]`

#### 获取消息

```
POST /message.get
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `channel_id` | string | 频道 ID |
| `message_id` | string | 消息 ID |

**响应：** `Message`。必需资源：`channel`、`user`。

#### 撤回消息

```
POST /message.delete
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `channel_id` | string | 频道 ID |
| `message_id` | string | 消息 ID |

#### 编辑消息

```
POST /message.update
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `channel_id` | string | 频道 ID |
| `message_id` | string | 消息 ID |
| `content` | string | 消息内容 |

#### 获取消息列表

```
POST /message.list
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `channel_id` | string | 频道 ID |
| `next` | string? | 分页令牌（默认从最新消息开始） |
| `direction` | Direction? | 查询方向（默认 `before`） |
| `limit` | number? | 消息数量限制（推荐 50） |
| `order` | Order? | 排序方式（默认 `asc`） |

**响应：** `Message` 的双向分页列表。必需资源：`user`。

> **注意：** 使用返回值中 `prev`/`next` 的存在性判断是否有更多数据，而非依赖 `data` 长度。

### 事件

| 事件名 | 描述 |
|--------|------|
| `message-created` | 消息被创建时触发 |
| `message-updated` | 消息被编辑时触发 |
| `message-deleted` | 消息被删除时触发 |

---

## 群组 (Guild)

### 类型定义

| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | 群组 ID |
| `name` | string? | 群组名称 |
| `avatar` | string? | 群组头像 |

### API

#### 获取群组

```
POST /guild.get
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `guild_id` | string | 群组 ID |

**响应：** `Guild` 对象。

#### 获取群组列表

```
POST /guild.list
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `next` | string? | 分页令牌 |

**响应：** `Guild` 的分页列表。

#### 处理群组邀请

```
POST /guild.approve
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `message_id` | string | 请求 ID |
| `approve` | boolean | 是否通过请求 |
| `comment` | string? | 备注信息 |

### 事件

| 事件名 | 描述 |
|--------|------|
| `guild-added` | 群组被创建或变得对 SDK 可见时触发 |
| `guild-updated` | 群组信息更新时触发 |
| `guild-removed` | 群组被删除或变得对 SDK 不可见时触发 |
| `guild-request` | 接收到新的入群邀请时触发 |

---

## 群组成员 (GuildMember)

### 类型定义

| 字段 | 类型 | 描述 |
|------|------|------|
| `user` | User? | 用户对象 |
| `nick` | string? | 用户在群组中的名称 |
| `avatar` | string? | 用户在群组中的头像 |
| `joined_at` | number? | 加入时间 |
| `roles` | GuildRole[]? | 成员的角色列表 |

### API

#### 获取群组成员

```
POST /guild.member.get
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `guild_id` | string | 群组 ID |
| `user_id` | string | 用户 ID |

**响应：** `GuildMember` 对象。

#### 获取群组成员列表

```
POST /guild.member.list
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `guild_id` | string | 群组 ID |
| `next` | string? | 分页令牌 |

**响应：** `GuildMember` 的分页列表。

#### 踢出群组成员

```
POST /guild.member.kick
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `guild_id` | string | 群组 ID |
| `user_id` | string | 用户 ID |
| `permanent` | boolean? | 是否永久踢出（无法再次加入） |

#### 禁言群组成员（实验性）

```
POST /guild.member.mute
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `guild_id` | string | 群组 ID |
| `user_id` | string | 用户 ID |
| `duration` | number | 禁言时长（毫秒），`0` 表示解除禁言 |

#### 通过群组成员申请

```
POST /guild.member.approve
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `message_id` | string | 请求 ID |
| `approve` | boolean | 是否通过请求 |
| `comment` | string? | 备注信息 |

### 事件

| 事件名 | 描述 |
|--------|------|
| `guild-member-added` | 群组成员增加时触发 |
| `guild-member-updated` | 群组成员信息更新时触发 |
| `guild-member-removed` | 群组成员移除时触发 |
| `guild-member-request` | 接收到新的加群请求时触发 |

---

## 群组角色 (GuildRole)

### 类型定义

| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | 角色 ID |
| `name` | string? | 角色名称 |

### API

#### 设置群组成员角色

```
POST /guild.member.role.set
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `guild_id` | string | 群组 ID |
| `user_id` | string | 用户 ID |
| `role_id` | string | 角色 ID |

#### 取消群组成员角色

```
POST /guild.member.role.unset
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `guild_id` | string | 群组 ID |
| `user_id` | string | 用户 ID |
| `role_id` | string | 角色 ID |

#### 获取角色列表

```
POST /guild.role.list
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `guild_id` | string | 群组 ID |
| `next` | string? | 分页令牌 |

**响应：** `GuildRole` 的分页列表。

#### 创建角色

```
POST /guild.role.create
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `guild_id` | string | 群组 ID |
| `role` | GuildRole | 角色数据 |

**响应：** `GuildRole` 对象。

#### 修改角色

```
POST /guild.role.update
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `guild_id` | string | 群组 ID |
| `role_id` | string | 角色 ID |
| `role` | GuildRole | 角色数据 |

#### 删除角色

```
POST /guild.role.delete
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `guild_id` | string | 群组 ID |
| `role_id` | string | 角色 ID |

### 事件

| 事件名 | 描述 |
|--------|------|
| `guild-role-created` | 群组角色被创建时触发 |
| `guild-role-updated` | 群组角色被修改时触发 |
| `guild-role-deleted` | 群组角色被删除时触发 |

---

## 好友 (Friend)

### 类型定义

| 字段 | 类型 | 描述 |
|------|------|------|
| `user` | User? | 用户对象 |
| `nick` | string? | 好友昵称 |

### API

#### 获取好友列表

```
POST /friend.list
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `next` | string? | 分页令牌 |

**响应：** `Friend` 的分页列表。

#### 删除好友

```
POST /friend.delete
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `user_id` | string | 用户 ID |

#### 处理好友申请

```
POST /friend.approve
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `message_id` | string | 请求 ID |
| `approve` | boolean | 是否通过请求 |
| `comment` | string? | 备注信息 |

### 事件

| 事件名 | 描述 |
|--------|------|
| `friend-request` | 接收到新的好友申请时触发 |

---

## 表态 (Reaction)

### API

#### 添加表态

```
POST /reaction.create
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `channel_id` | string | 频道 ID |
| `message_id` | string | 消息 ID |
| `emoji_id` | string | 表情 ID |

#### 删除表态

```
POST /reaction.delete
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `channel_id` | string | 频道 ID |
| `message_id` | string | 消息 ID |
| `emoji_id` | string | 表情 ID |
| `user_id` | string? | 用户 ID（不传表示删除自己的表态） |

#### 清除表态

```
POST /reaction.clear
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `channel_id` | string | 频道 ID |
| `message_id` | string | 消息 ID |
| `emoji_id` | string? | 表情 ID（不传表示清除所有表态） |

#### 获取表态列表

```
POST /reaction.list
```

| 参数 | 类型 | 描述 |
|------|------|------|
| `channel_id` | string | 频道 ID |
| `message_id` | string | 消息 ID |
| `emoji_id` | string | 表情 ID |
| `next` | string? | 分页令牌 |

**响应：** `User` 的分页列表。

### 事件

| 事件名 | 描述 |
|--------|------|
| `reaction-added` | 表态被添加时触发 |
| `reaction-removed` | 表态被移除时触发 |

---

## 登录信息 (Login)

### 类型定义

| 字段 | 类型 | 描述 |
|------|------|------|
| `sn` | number | 序列号（实验性，仅标识 Login 对象，与平台无关） |
| `platform` | string? | 平台名称 |
| `user` | User? | 用户对象 |
| `status` | LoginStatus | 登录状态 |
| `adapter` | string | 适配器名称（实验性） |
| `features` | string[]? | 平台特性列表（实验性） |

> **注意：**
> - `login.sn` 仅用于标识 Login 对象，不进行持久化
> - `login.user` 不一定是真实用户，也可以是机器人或应用身份
> - 所有非登录事件中 `login` 均处于 `ONLINE` 状态，因此 `platform` 和 `user` 总是有值

#### LoginStatus 枚举

| 名称 | 值 | 描述 |
|------|:---:|------|
| `OFFLINE` | 0 | 离线 |
| `ONLINE` | 1 | 在线 |
| `CONNECT` | 2 | 正在连接 |
| `DISCONNECT` | 3 | 正在断开连接 |
| `RECONNECT` | 4 | 正在重新连接 |

### API

#### 获取登录信息

```
POST /login.get
```

无请求参数。

**响应：** `Login` 对象。

### 事件

| 事件名 | 描述 |
|--------|------|
| `login-added` | 登录被创建时触发 |
| `login-removed` | 登录被删除时触发 |
| `login-updated` | 登录信息更新时触发 |

---

## 表情 (Emoji) (实验性)

### 类型定义

| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | 表情 ID |
| `name` | string? | 表情名称 |

### 事件

| 事件名 | 描述 |
|--------|------|
| `guild-emoji-added` | 群组表情被添加时触发 |
| `guild-emoji-updated` | 群组表情被更新时触发 |
| `guild-emoji-deleted` | 群组表情被删除时触发 |

---

## 交互 (Interaction) (实验性)

交互功能主要通过机器人提供，由用户在聊天应用中触发。

### 类型定义

#### Argv

| 字段 | 类型 | 描述 |
|------|------|------|
| `name` | string | 指令名称 |
| `arguments` | array | 参数 |
| `options` | object | 选项 |

#### Button

| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | 按钮 ID |

### 事件

| 事件名 | 描述 |
|--------|------|
| `interaction/button` | 按钮被点击时触发，必需资源：`button` |
| `interaction/command` | 调用斜线指令时触发，必需资源：`argv` 或 `message` 中至少包含其一 |

---

## 事件列表

以下是 Satori 协议定义的所有事件的汇总：

| 资源 | 事件名 | 说明 |
|:----:|--------|------|
| Channel | `channel-added` | 频道被创建或可见 |
| Channel | `channel-updated` | 频道信息更新 |
| Channel | `channel-removed` | 频道被删除或不可见 |
| Message | `message-created` | 消息被创建 |
| Message | `message-updated` | 消息被编辑 |
| Message | `message-deleted` | 消息被删除 |
| Guild | `guild-added` | 群组被创建或可见 |
| Guild | `guild-updated` | 群组信息更新 |
| Guild | `guild-removed` | 群组被删除或不可见 |
| Guild | `guild-request` | 入群邀请 |
| GuildMember | `guild-member-added` | 成员增加 |
| GuildMember | `guild-member-updated` | 成员信息更新 |
| GuildMember | `guild-member-removed` | 成员移除 |
| GuildMember | `guild-member-request` | 加群请求 |
| GuildRole | `guild-role-created` | 角色被创建 |
| GuildRole | `guild-role-updated` | 角色被修改 |
| GuildRole | `guild-role-deleted` | 角色被删除 |
| Friend | `friend-request` | 好友申请 |
| Reaction | `reaction-added` | 表态添加 |
| Reaction | `reaction-removed` | 表态移除 |
| Login | `login-added` | 登录创建 |
| Login | `login-removed` | 登录删除 |
| Login | `login-updated` | 登录信息更新 |
| Emoji | `guild-emoji-added` | 表情添加 |
| Emoji | `guild-emoji-updated` | 表情更新 |
| Emoji | `guild-emoji-deleted` | 表情删除 |
| Interaction | `interaction/button` | 按钮点击 |
| Interaction | `interaction/command` | 斜线指令 |

---

## API 端点速查表

| API | 方法 | 说明 |
|-----|:----:|------|
| `/user.get` | POST | 获取用户信息 |
| `/channel.get` | POST | 获取频道 |
| `/channel.list` | POST | 获取频道列表 |
| `/channel.create` | POST | 创建频道 |
| `/channel.update` | POST | 修改频道 |
| `/channel.delete` | POST | 删除频道 |
| `/channel.mute` | POST | 禁言频道（实验性） |
| `/user.channel.create` | POST | 创建私聊频道 |
| `/message.create` | POST | 发送消息 |
| `/message.get` | POST | 获取消息 |
| `/message.delete` | POST | 撤回消息 |
| `/message.update` | POST | 编辑消息 |
| `/message.list` | POST | 获取消息列表 |
| `/guild.get` | POST | 获取群组 |
| `/guild.list` | POST | 获取群组列表 |
| `/guild.approve` | POST | 处理群组邀请 |
| `/guild.member.get` | POST | 获取群组成员 |
| `/guild.member.list` | POST | 获取群组成员列表 |
| `/guild.member.kick` | POST | 踢出群组成员 |
| `/guild.member.mute` | POST | 禁言群组成员（实验性） |
| `/guild.member.approve` | POST | 通过群组成员申请 |
| `/guild.member.role.set` | POST | 设置成员角色 |
| `/guild.member.role.unset` | POST | 取消成员角色 |
| `/guild.role.list` | POST | 获取角色列表 |
| `/guild.role.create` | POST | 创建角色 |
| `/guild.role.update` | POST | 修改角色 |
| `/guild.role.delete` | POST | 删除角色 |
| `/friend.list` | POST | 获取好友列表 |
| `/friend.delete` | POST | 删除好友 |
| `/friend.approve` | POST | 处理好友申请 |
| `/reaction.create` | POST | 添加表态 |
| `/reaction.delete` | POST | 删除表态 |
| `/reaction.clear` | POST | 清除表态 |
| `/reaction.list` | POST | 获取表态列表 |
| `/login.get` | POST | 获取登录信息 |
