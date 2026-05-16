# wxauto → Hermes qljk 桥接 Handover

## 概述

用 wxauto (UIAutomation) 操控 Windows 微信 PC 客户端 3.9.x，轮询未读消息 → 调用 WSL2 内 Hermes qljk Agent API → 发送回复。

```
WeChat PC (Windows) → wxauto → bridge.py → HTTP :8647 → WSL2 Hermes qljk
```

**不修改任何 Hermes 配置。**

---

## 文件清单

```
C:\Users\ohmyc\.openclaw\workspace\wxauto_bridge\
├── wxauto_bridge.py          # 主脚本（单文件，~900行）
├── start_bridge.bat           # 启动批处理（双击运行）
├── README.md                  # 项目文档
├── HANDOVER.md                # 本文件（踩坑记录）
├── docs/
│   └── DAEMON_PLAN.md         # 守护进程策划文档
└── data/
    ├── bridge.log             # 运行日志（排查问题看这个）
    ├── conversation_history.json  # 会话上下文存储
    ├── seen_messages.json     # 已处理消息指纹（防重复）
    ├── bridge_state.json      # 日回复计数器
    └── bridge.pid             # 进程锁（防止多实例）
```

---

## 启动 / 停止

### 启动（需满足前提条件）

1. 微信 PC 客户端已登录
2. WSL2 内 Hermes qljk gateway 运行中 (`hermes gateway run --profile qljk`)

**方式一：双击 `start_bridge.bat`**

**方式二：命令行**
```
"C:\Program Files\Python312\python.exe" C:\Users\ohmyc\.openclaw\workspace\wxauto_bridge\wxauto_bridge.py
```

### 停止
- 在 bridge 终端窗口按 `Ctrl+C`（会保存状态后退出）
- 或 `taskkill /PID <pid>` 杀掉进程（查看 `data\bridge.pid` 获取 PID）
- **警告**: 手动删除 `bridge.pid` 然后重启会导致多实例并行，必须先用 `taskkill /F /PID` 杀掉所有 Python 进程

---

## 核心设计决策 & 踩过的坑

### 1. PID 文件锁 → 防止多实例互抢
**问题**: 多个 Python 进程同时运行，互相消耗对方的未读红点，消息被吞。更严重的是多个进程同时回复同一会话，造成重复回复。
**方案**: 启动时写 `bridge.pid`，检查旧 PID 是否存活。只允许一个实例。
**教训 (2026-05-13)**: 手动删除 `bridge.pid` 同时旧进程未杀干净，导致 3 个 Python 进程并行运行，同一消息被多次回复。

### 2. ChatWith RegexName 误匹配 → 完全禁用，Sibling 遍历 + 纯净 Name 精确匹配
**问题**: `wx.ChatWith("Liuying")` 内部用 `RegexName=who` 匹配 UI 控件。
"Liuying" 模糊匹配到了 "Liuying、』婉~一璐相伴』"（群聊），导致打开错误会话。
**方案**: 完全移除 `ChatWith` 调用。改用 Sibling 遍历获取 ListItemControl 引用，提取内部
ButtonControl.Name（wxauto `GetSessionAmont` 同款逻辑）做精确匹配。
ListItemControl.Name 含 "X条新消息"/"已置顶" 等 UI 后缀，不能直接比较。

### 3. SessionStore 复合 Key → 群聊每人独立上下文
**问题**: 群聊中多人发言，context key 只用聊天名会导致所有人共享上下文。
**方案**: Key = `"{聊天名}|{说话人}"`，如 `"测试自动回复|Liuying"`。
同一群聊不同人各自独立上下文。`session_id` 和指纹也同步使用 `{聊天名}|{说话人}` 确保一致。

### 4. MINIMIZE_ON_START=False → SendMsg 需要窗口存在
**问题**: 启动后最小化微信窗口 → `SendMsg` 报 `SetWindowPos 无效的窗口句柄`。
**方案**: `MINIMIZE_ON_START = False`。微信窗口必须保持存在（可以覆盖在其他窗口下面）。

### 5. 日期上下文注入 → 防止模型编造时间
**问题**: qljk 模型不知道当前日期，用户问"今天北京限行"时编造了 2025 年和错误的尾号。
**方案**: `call_hermes()` 自动注入 `当前时间: 2026年05月13日 15:41 (星期三)`。

### 6. false positive unread → 会话级回复冷却 + Self 过滤
**现象**: 桥接回复后，微信有时把"已发出的回复"计入未读红点。`GetSessionList`
一直返回 `{Liuying: 1}` 但 `GetAllMessage` 没有新的有效消息。
**方案 (2026-05-13)**:
- `PER_SESSION_REPLY_COOLDOWN = 30s`：同一会话回复后 30 秒内不再回复
- Self 消息过滤：`sender == 'Self'` 的消息直接跳过
- 仍有残留但已被冷却机制兜底

### 7. 串行处理 → 长耗时 API 调用阻塞其他会话
**现象**: `process_unread` 一个会话一个串行处理。Hermes 搜索类请求耗时 2-3 分钟。
期间其他会话的新消息延迟到下一轮轮询才处理。
**当前配置**: `MAX_SESSIONS_PER_CYCLE = 3`，`SESSION_SWITCH_COOLDOWN = 5s`

### 8. GetSessionList 时序竞态 → 私聊/群聊消息归属错误
**现象**: 用户同时在私聊和群聊发消息。`GetSessionList` 第一轮只返回了一个会话。
**后果**: bridge 在某个会话中读到的消息可能被归属于错误窗口。
**缓解 (2026-05-13)**:
- `ALLOW_SESSIONS` 白名单：只回复指定会话，忽略其余所有
- `chat_name` 交叉验证：实际打开窗口名与 GetSessionList 返回名不一致时跳过
- 回复用 `chat_name` 而非 `who`，确保发到正确窗口

### 9. AVOID_FOREGROUND 实际生效
**问题**: 之前 `AVOID_FOREGROUND = True` 定义了但从未使用。`wx._show()` 每个会话都无条件调用。
**方案 (2026-05-13)**: `_show()` 移到 `GetAllMessage` 前且由 `AVOID_FOREGROUND` 控制。默认不前置窗口。

### 10. 启动预热 → 跳过离线积压消息
**问题**: bridge 重启后会回复离线期间积压的所有未读消息（可能已过时/不相关）。
**方案**: `prime_startup_messages()` 函数，启动时遍历所有未读会话标记已读不回复。

### 11. 只回复最新一条 → 避免批量回复过时消息
**问题**: 多条未读消息时，bridge 逐一回复所有历史消息。这些消息可能已过时，
批量回复也容易触发风控。
**方案**: 每轮每个会话只处理最新一条未读消息，其余标记已读跳过。

### 12. GetSessionList 日志节流 → 避免刷屏
**问题**: false positive unread 导致 `GetSessionList 返回 1 个会话: {'Liuying': 1}`
每 3 秒重复一次，日志刷屏。
**方案**: 只在会话列表变化时才输出日志，无变化时静默。

### 13. ListItemControl.Name 含 UI 后缀 → Sibling 遍历 + ButtonControl 提取纯净名
**问题**: `ListItemControl.Name` 包含 "X条新消息"/"已置顶"/"最后一条消息预览" 等
UI 渲染后缀。如 `Name="测试自动回复1条新消息"` 是原始值，`GetSessionList` 返回的
键名是纯净名 `"测试自动回复"`。`ListItemControl(Name="测试自动回复").Click()`
永远找不到（Name 不匹配），报 `Find Control Timeout`。
**根因**: wxauto `GetSessionAmont()`（line 176）从内部 `ButtonControl().Name` 提取纯净名。
**方案 (2026-05-13)**:
- 不再使用 `ListItemControl(Name=who)` 搜索
- 改用 Sibling 遍历（`GetNextSiblingControl()`）获得 ListItemControl 引用
- 每个 ListItemControl 提取内部 ButtonControl.Name 与目标精确比对
- 视口边缘项（特别是最后一个）BoundingRectangle 可能为零，走搜索框过滤路径
  确保目标出现在列表前部后遍历

---

## 关键配置

| 参数 | 值 | 说明 |
|------|-----|------|
| `HERMES_API_URL` | `http://127.0.0.1:8647/v1/chat/completions` | Hermes API |
| `HERMES_TIMEOUT` | 120s | Hermes API 超时（socket 级强超时） |
| `HERMES_LONG_CALL_THRESHOLD` | 60s | 超此值刷新 GetSessionList |
| `POLL_INTERVAL` | 3±2s | 轮询间隔（带随机抖动） |
| `REPLY_DELAY_MIN/MAX` | 0.5-2s | 收到消息后随机等待（Hermes 耗时已是天然延迟） |
| `DAILY_TOTAL_LIMIT` | 200 | 全天回复上限 |
| `PER_USER_HOURLY_LIMIT` | 20 | 单用户每小时上限 |
| `MAX_SESSIONS_PER_CYCLE` | 3 | 每轮最多处理会话数 |
| `PER_SESSION_REPLY_COOLDOWN` | 30s | 同一会话回复后冷却（防重复回复） |
| `SESSION_SWITCH_COOLDOWN` | 5s | 切换会话最短间隔 |
| `MAX_HISTORY_TURNS` | 10 | 每个会话保留最近 10 轮对话 |
| `SESSION_TTL_SECONDS` | 1800 | 空闲 30 分钟自动归档 |
| `ACTIVE_START_HOUR/END_HOUR` | 7/3 | 早7点-凌晨3点（跨午夜） |
| `MINIMIZE_ON_START` | False | 不能设为 True |
| `AVOID_FOREGROUND` | True | 不主动前置微信窗口 |
| `ALLOW_SESSIONS` | `set()` | 白名单（空=所有），只回复这些会话 |
| `SKIP_SESSIONS` | 微信团队/微信支付等 | 不回复的会话黑名单 |

> **启动预热已改为函数** `prime_startup_messages()`：启动时遍历所有未读会话，标记已读不回复。不再使用 `STARTUP_PRIME_SECONDS` 定时预热。

---

## 会话识别链路（2026-05-13 修复后）

```
GetSessionList → who (聊天名)
  → 白名单检查 (ALLOW_SESSIONS)
  → 黑名单检查 (SKIP_SESSIONS)
  → 会话冷却检查 (PER_SESSION_REPLY_COOLDOWN)
  → 精确打开 (ListItemControl Click)
  → chat_name 交叉验证 (chat_name == who)
  → GetAllMessage
  → sender 提取 (msg.sender)
  → 指纹去重 (sha256(chat_name|sender|content))
  → 上下文加载 (context_key = "{chat_name}|{sender}")
  → Hermes 调用 (session_id = sha256(chat_name|sender))
  → send_reply(wx, chat_name, reply)  ← 用实际打开的聊天名
```

**关键**: 回复目标永远是 `chat_name`（实际打开的窗口名），不是 `who`（GetSessionList 的 key）。

---

## 如何重置测试环境

1. 停掉 bridge: `taskkill /F /PID <pid>`
2. 检查无残留 Python 进程: `tasklist | findstr python`
3. 清空 `data/conversation_history.json` → `{"sessions": {}, "last_active": {}}`
4. 清空 `data/seen_messages.json` → `[]`
5. 清空 `data/bridge_state.json` → `{"daily_total": 0, "daily_date": "2026-05-13"}`
6. 删除 `data/bridge.pid`
7. **在微信中手动清除各聊天的聊天记录**
8. 重启 bridge

---

## 依赖环境

- **Python**: C:\Program Files\Python312\python.exe (Python 3.12)
- **wxauto**: `pip install wxauto` (依赖 pywin32, comtypes)
- **WSL2 Hermes**: qljk profile, gateway 监听 127.0.0.1:8647
- **微信**: PC 客户端 3.9.x，已登录

---

## Hermes 端（WSL2）

- Profile: `~/.hermes/profiles/qljk/`
- SOUL: `~/.hermes/profiles/qljk/SOUL.md`（千流健康系统智能助手）
- 模型: MiniMax-M2.7 (minimax-cn provider)
- 启动: `hermes gateway run --profile qljk`（必须用 run，systemd 不可用）
- sessions: `~/.hermes/profiles/qljk/sessions/`（Hermes 自动管理，bridge 不触碰）
- **注意**: Hermes 忽略 bridge 传入的 `session_id`，每次都创建新的 `api-{hash}` 会话。
  Bridge 通过 `conversation_history.json` 本地维护上下文，注入到 `messages` 数组。

---

## 已知未解决问题

| # | 问题 | 严重程度 | 缓解措施 |
|----|------|---------|---------|
| 1 | false positive unread: bridge 回复后微信自报红点 | 低 | 会话冷却 30s + Self 过滤已兜底 |
| 2 | 串行阻塞: 长 API 调用延迟其他会话 | 中 | while-loop 动态刷新 + 120s socket 超时 |
| 3 | 未读红点被微信自动消耗 | 低 | 白名单 + 冷却减少影响 |
| 4 | Hermes 故障时消息永久丢失（GetAllMessage 已消耗红点） | 中 | 依赖 watchdog 监控 Hermes 健康 |

---

## 守护进程（规划中，未实施）

详见 `docs/DAEMON_PLAN.md`。

**三层守护策略**:
1. **WSL 层**: 7 个 WSL 服务 systemd 化（`Restart=always`），替换当前 nohup
2. **Windows 层**: `watchdog.ps1` 每分钟检查 bridge 进程 + Hermes :8647 可达性，自动重启
3. **Bridge 韧性**: Hermes 启动检查改重试（不再 `sys.exit(1)`），加微信断连检测

**当前兜底**:
- Hermes 不可达时 bridge 日志报错，不会退出（非致命错误）
- 连续 UIA 错误 >10 次 bridge 才退出，watchdog 会重启
- 微信必须手动登录（不可绕过）

---

## 修复记录

### 2026-05-13 (第二轮)
1. **指纹含 sender** — `msg_fingerprint(who, sender, content)` 防止同群不同人同内容冲突
2. **session_id 含 sender** — 发给 Hermes 的 session_id 对齐本地 context_key
3. **Self 消息过滤** — `sender == 'Self'` 直接跳过
4. **ALLOW_SESSIONS 白名单** — 只回复指定会话，防止串群
5. **chat_name 交叉验证** — 实际打开窗口名不匹配则跳过
6. **回复用 chat_name** — `send_reply(wx, chat_name, reply)` 而非 `who`
7. **PER_SESSION_REPLY_COOLDOWN** — 回复后 30 秒冷却
8. **AVOID_FOREGROUND 生效** — `_show()` 不再无条件调用
9. **启动预热 STARTUP_PRIME_SECONDS** — 启动 60 秒内不回复
10. **只回复最新一条** — 每轮每会话只处理 1 条最新消息
11. **GetSessionList 日志节流** — 无变化时静默
12. **PID 锁不变但加文档警告** — 不能手动删 pid 文件

### 2026-05-13 (第三轮 — UIA Name 精确匹配修复)
1. **ListItemControl.Name != 纯净会话名** — Name 含 "X条新消息"/"已置顶" 等后缀，
   不能用 `ListItemControl(Name=who)` 直接搜索（永远 `Find Control Timeout`）
2. **Sibling 遍历替代 Name 搜索** — 用 `GetNextSiblingControl()` 遍历 ListItemControl，
   提取 `ButtonControl().Name` 获取纯净名（与 wxauto `GetSessionAmont` 逻辑一致）
3. **搜索框过滤作为兜底路径** — 目标在视口边缘时 BoundingRectangle 为零，先通过搜索框
   过滤（`B_Search.Click` → `SendKeys(who)` → `ENTER`），再遍历
4. **移除 ChatWith fallback** — 不降级到 RegexName 匹配，完全使用精确 Name 匹配
5. **修复 UIA SendKeys 键名** — `{Backspace}` 不存在于 SpecialKeyNames，正确名称是 `{BACK}`
6. **已知问题 #4 移除** — UIA Name 提取问题已根本解决，不再是无会话级消息查询的问题

### 2026-05-13 (第四轮 — 会话时序漂移修复)
1. **标题稳定等待 `_wait_chat_stable()`** — 连续两次 UIA 读取标题都匹配才认为窗口切换完成，
   防止过渡动画中途读取消息面板（最多等待 2s）
2. **读取后回校验** — `GetAllMessage` 后再次 `verify_chat_window`，窗口漂移则丢弃本轮
3. **for-loop → while-loop + 动态刷新** — 长 Hermes 调用 (>60s) 后重新 `GetSessionList`，
   阻塞期间新出现的会话可追加到处理队列

### 2026-05-13 (第五轮 — 阻塞控制)
1. **Hermes 超时 300s → 120s** — 不会无限阻塞其他会话
2. **Socket 级超时** — `socket.setdefaulttimeout(120)` 模块级全局设置；
   `urlopen(timeout=)` 对已建立连接的 read 阶段不生效，OS 级 socket 超时才能强制切断
3. **`call_hermes` 返回 `(reply, elapsed)`** — 调用方可感知实际耗时
4. **回复延迟缩短** — `REPLY_DELAY 3-8s → 0.5-2s`，Hermes 2-6 分钟已是天然延迟

### 2026-05-13 (第六轮 — @提及过滤 + 搜索 ENTER 修复)
1. **@提及过滤器** — 消息中提取 `@name`，若 @了非 bot 昵称的人则标记已读不回复
   (`re.findall(r'@\S+', content)`)
2. **搜索框 `{ENTER}` 移除** — 微信搜索即时过滤，按 ENTER 会打开第一个搜索结果（可能串群），
   两处均已移除（`_open_session` + `prime_startup_messages`）
3. **@过滤日志** — 被过滤的消息输出 `@过滤` 日志行，方便验证
