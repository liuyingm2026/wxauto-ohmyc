# wxauto-ohmyc — 微信机器人桥接项目

通过 Windows UIAutomation 操控 PC 微信，将微信消息桥接到 LLM Agent 后端。

## 项目结构

```
wxauto-ohmyc/
├── wxauto.py            # UIA 封装库（操控微信客户端）
├── uiautomation.py      # 底层 UIA COM 封装
├── elements.py          # UI 元素定位
├── color.py             # 像素颜色检测
├── utils.py             # 工具函数
├── languages.py         # 多语言支持
├── errors.py            # 异常定义
├── bridge/              # 桥接应用
│   ├── wxauto_bridge.py # 主程序：消息轮询 → LLM 推理 → 消息投递
│   ├── start_bridge.bat # Windows 启动脚本
│   ├── HANDOVER.md      # 踩坑记录
│   └── docs/
│       ├── TROUBLESHOOTING.md  # 问题分析
│       └── DAEMON_PLAN.md      # 守护进程策划
```

## 架构概览

```
微信 PC 客户端 ←→ wxauto (UIA) ←→ wxauto_bridge.py ←→ HTTP ←→ Hermes Agent (:8647)
                                        │
                                        ├── Phase 1: 消息采集（打开会话→读消息→返回列表）
                                        ├── Phase 2: LLM 推理（HTTP 调用 Hermes API）
                                        └── Phase 3: 消息投递（打开会话→发消息→返回列表）
```

### 核心流程

```
┌──────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  GetSession  │ →  │   采集未读消息    │ →  │   Hermes 推理    │
│  List(...)   │    │  (每会话取最新)    │    │  (批量 HTTP)     │
└──────────────┘    └──────────────────┘    └──────────────────┘
                                                    │
                                                    ▼
┌──────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  返回聊天     │ ←  │   投递回复消息    │ ←  │   生成回复文本    │
│  列表        │    │  (分段发送)       │    │                  │
└──────────────┘    └──────────────────┘    └──────────────────┘
```

- 每轮轮询间隔: **3±2 秒**（随机抖动）
- 每轮最多处理: **3 个会话**
- Hermes 超时: **120 秒**

## 配置参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `HERMES_API_URL` | `http://127.0.0.1:8647/v1/chat/completions` | Hermes Qljk API |
| `PER_USER_HOURLY_LIMIT` | 20 | 单用户每小时上限 |
| `DAILY_TOTAL_LIMIT` | 200 | 全天总回复上限 |
| `PER_SESSION_REPLY_COOLDOWN` | 30s | 同会话回复冷却 |
| `SESSION_SWITCH_COOLDOWN` | 5s | 切换会话最短停留 |
| `EMPTY_READ_COOLDOWN` | 45s | 空读冷却 |
| `FORCE_REFRESH_INTERVAL` | 30s | 强制回到聊天列表间隔 |
| `MAX_REPLY_LENGTH` | 2000 | 微信单条上限 |
| `MAX_HISTORY_TURNS` | 10 | 每会话保留对话轮数 |
| `SESSION_TTL_SECONDS` | 1800 | 会话 30 分钟过期 |
| `ACTIVE_START_HOUR` | 7 | 活跃开始时间 |
| `ACTIVE_END_HOUR` | 3 | 活跃结束时间（跨午夜） |

## 风控机制

### 频率控制
- 单用户每小时最多 20 条回复
- 全日最多 200 条
- 回复延迟 0.5-2s 随机化

### 消息去重
- SHA256 指纹: `sha256(chat|sender|content|date)[:16]`
- 最近 500 条指纹保存在 `data/seen_messages.json`

### 会话上下文
- 按 `{chat_name}|{sender}` 隔离上下文
- 群聊中每人独立上下文
- 空闲 30 分钟自动归档

### @提及过滤
- 群聊中 @非 bot 昵称的消息跳过

### 会话检测
- `GetSessionList(newmessage=True)` 获取未读会话
- `_last_session_counts` 快照对比，仅 count 增加才打开
- 回复后清零该会话计数，防止残留 badge 导致跳过

### 窗口漂移防护
- `_wait_chat_stable()`: 连续两次 UIA 标题匹配才确认切换完成
- `verify_chat_window()`: GetAllMessage 后回校验，漂移则丢弃
- while-loop 动态刷新: Hermes 长调用后刷新 GetSessionList

## 启动方式

```bash
# Windows PowerShell
Start-Process -WindowStyle Hidden `
  -FilePath "C:\Program Files\Python312\python.exe" `
  -ArgumentList "C:\Users\ohmyc\.openclaw\workspace\wxauto_bridge\wxauto_bridge.py"
```

或双击 `start_bridge.bat`。

**前置条件**:
1. 微信 PC 客户端已登录
2. WSL2 Hermes Qljk Gateway 已启动（端口 8647）

## 所依赖的 wxauto 修改

基于 [cluic/wxauto](https://github.com/cluic/wxauto) 的 fork，本项目的 `wxauto.py` 包含以下关键修改：

| 修改 | 位置 | 说明 |
|------|------|------|
| `GetSessionList(newmessage=True)` | `wxauto.py` | 只返回有未读消息的会话 |
| `A_ChatIcon` 聊天图标 | `wxauto.py` | 双击回到聊天列表 |
| `B_Search` 搜索框 | `wxauto.py` | 搜索框无 ENTER 确认 |
| `GetAllMessage` | `wxauto.py` | 返回完整消息列表含 sender |
| `RegexName` 支持 | `wxauto.py` | SessionBox.ListItemControl 正则名称匹配 |
| `ChatBox.TextControl` | `wxauto.py` | 读取顶部聊天窗口标题 |

## 已知限制

1. **UIA 不可靠**: WeChat 3.9.x 的 UIA 控件树在不同状态下行为不一致
2. **窗口操作必然抢焦点**: UIA 操作（Click、DoubleClick）在 Windows 上会激活窗口
3. **无守护进程**: bridge 崩溃后不会自动恢复
4. **Hermes 超时时消息丢失**: GetAllMessage 消耗红点后才调用 Hermes，超时则消息被吞
5. **依赖 WeChat PC**: 微信 PC 客户端必须登录并保持运行

## 相关文档

- [HANDOVER.md](bridge/HANDOVER.md) — 开发过程中的踩坑记录
- [TROUBLESHOOTING.md](bridge/docs/TROUBLESHOOTING.md) — 问题分析与修复历史
- [DAEMON_PLAN.md](bridge/docs/DAEMON_PLAN.md) — 守护进程策划
