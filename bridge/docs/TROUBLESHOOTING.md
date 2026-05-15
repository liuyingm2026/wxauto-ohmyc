# wxauto Bridge 问题分析与修复历史

> 最后更新：2026-05-15

## 一、今日日志复盘（2026-05-15）

### 1. 正常工作时（02:52-02:56）

```
02:52:51 会话变化: {'测试自动回复': 1}  → 回复群聊
02:53:03 会话变化: {'Liuying': 1}        → 回复私聊
02:53:35 会话变化: {'测试自动回复': 1}  → 回复群聊
02:53:48 会话变化: {'Liuying': 1}        → 回复私聊
```

群聊和私聊**交替被检测**，各自独立回复，工作正常。这是修改代码之前的版本。

### 2. 私聊丢失（11:48-11:52）

```
11:51:53 本轮: Phase1=1 Phase2=1 Phase3=1 | 今日 6/200
11:51:58 跳过 [Liuying]: 未读数 1 ≤ 上次 1 (无新消息)
11:52:02 跳过 [Liuying]: 未读数 1 ≤ 上次 1 (无新消息)
...连续跳过 9 次...
```

**根因**: `_last_session_counts` 未清零 bug。回复后 badge 数不变，下次 Poll 判定无新消息。

### 3. 完全失明（17:39-18:25）

修改移除 `A_ChatIcon.DoubleClick()` 后，`GetSessionList` 静默返回空 `{}`（不抛异常），bridge 以为没有任何未读消息。后续加入 30s 懒刷新修复。

### 4. 只检测群聊不检测私聊（18:26-至今）

```
18:26:21 会话变化: {'测试自动回复': 1}   ← 只有群聊
18:30:34 会话变化: {'测试自动回复': 2}   ← 只有群聊
18:31:36 Phase1 采集 [测试自动回复] ...
```

**私聊 "Liuying" 从未出现**。但修改前的 02:52 版本和 11:48 版本都能同时检测到两个会话。

---

## 二、核心 Bug 分析

### Bug 1: 会话路由错误 — 私聊/群聊混淆（当前未解决）

**现象**: 私聊 "Liuying" 的消息，bridge 的回复出现在群聊 "测试自动回复" 中。

**可能原因**:

| 假设 | 可能性 | 证据 |
|------|--------|------|
| a) GetSessionList 未返回 "Liuying"，bridge 只看到群聊 | **高** | 18:26 后日志从未出现 "Liuying" |
| b) `_open_session` RegexName 匹配错误，打开的是群聊 | 中 | 修改前工作正常，RegexName pattern 未变 |
| c) 微信 UIA 状态不一致，两个会话的 unread badge 串了 | 中 | WeChat 3.9.x UIA 树已知不稳定 |
| d) prime_startup_messages 将两条私聊消息指纹了一并消费 | 低 | prime 只标记已读不回复 |

**最可能根因 (a) 的详细分析**:

对比修改前后的 `process_unread` 行为：

| 时间窗口 | GetSessionList 结果 | `_open_session` 行为 |
|----------|-------------------|----------------------|
| 02:52-02:56（修改前） | 交替返回 `{Liuying}` 和 `{测试自动回复}` | 分别正确打开 |
| 18:26-18:34（修改后） | **只**返回 `{测试自动回复}` | 只打开群聊 |

差别：
1. 修改前：每轮 Poll 都先 `A_ChatIcon.DoubleClick()` 再 `GetSessionList`
2. 修改后：直接 `GetSessionList`，空结果时才做 DoubleClick（30s 间隔）

**假设**: `GetSessionList(newmessage=True)` 的行为可能受当前聊天窗口状态影响。当用户正在查看私聊 "Liuying" 时，该会话的 unread badge 被 WeChat 消费了，`newmessage=True` 不再返回它。但群聊 "测试自动回复" 的 badge 因为用户"离开"了（切到了私聊），所以仍然显示。

但这不能解释为什么修改前交替检测成功——修改前每 3s 做一次 DoubleClick 返回聊天列表，badge 应该是稳定的。

**实际更可能的解释**: WeChat UIA 的 `GetSessionList(newmessage=True)` 本身就是**不可靠的**。有时返回 `{Liuying:1, 测试自动回复:1}`，有时只返回其中一个。修改前后的差异可能是**时间窗口巧合**，不是代码逻辑差异。

### Bug 2: `_last_session_counts` 残留 — 回复后消息被跳过

**现象**: bridge 回复了一条消息后，WeChat badge 仍显示同一数值（如 1），下轮 Poll 判定 `count ≤ last` 跳过。

**根因**:
```python
# 第 922 行：用原始 GetSessionList 结果覆盖，无视 Phase3 的清零
_last_session_counts = dict(sessions)
```

**时间线**:
- 2026-05-13: 引入 `_last_session_counts` 防止 false positive unread
- 2026-05-15 11:52: 私聊连续跳过 9 次
- 2026-05-15 18:30: 修复——Phase3 成功后清零 `_last_session_counts[who] = 0`，且合并时保留清零

**状态**: 已修复，需验证持续有效性。

### Bug 3: GetSessionList 盲区 — 无 DoubleClick 时返回空

**现象**: `GetSessionList(newmessage=True)` 不抛异常但返回 `{}`，bridge 无法检测任何新消息。

**根因**: 当微信停留在某个聊天窗口内部（而非聊天列表）时，UIA 控件树的 `SessionBox` 不可遍历。

**时间线**:
- 2026-05-13: 发现 `GetSessionList` 在聊天视图内会崩溃，加入 `A_ChatIcon.DoubleClick()` 在每次 Poll 前确保返回列表
- 2026-05-15 17:49: 为修复"无消息时抢窗口"，移除无条件 DoubleClick
- 2026-05-15 17:49-18:25: bridge 完全失明
- 2026-05-15 18:25: 改为 30s 懒刷新 —— 仅当 GetSessionList 持续返回空 30s+ 才做 DoubleClick

**当前方案的问题**: 30s 刷新间隔意味着私聊消息可能需要等最多 30s 才被检测到。而且刷新时仍然会抢窗口。

### Bug 4: 启动清积压与第一轮竞态

**现象**: bridge 启动后立即回复多条消息，"默认发一大堆"。

**根因**:
1. `prime_startup_messages` 标记积压消息为已读（指纹方式）
2. 主循环第一轮 Poll：`_last_session_counts = {}`（空字典）
3. `GetSessionList` 返回有未读的会话 → `count > 0 (last)` → 全部当作新消息处理
4. 但实际上这些消息大部分是 prime 阶段刚标记过的（积压消息），或者在 prime 和 第一轮 Poll 之间到达的

**关键问题**:
- `prime_startup_messages` 使用 `open_session → GetAllMessage → 指纹标记` 的方式
- 但 prime 过程中打开的会话可能**不包含所有有未读 badge 的会话**（prime 也依赖 GetSessionList）
- prime 结束后未做 `_last_session_counts` 基线保存
- 而且 prime 期间用户可能发送新消息

**状态**: 未修复。解决方案：
1. prime 完成后立即保存 `_last_session_counts` = 当前 GetSessionList 结果
2. 或增加启动冷却期（如启动后 30s 内不回复）

---

## 三、历史修复记录

### 第一轮（2026-05-13 下午）— 串群修复

| 问题 | 根因 | 修复 |
|------|------|------|
| 串群回复（A 群消息回复到 B 群） | `ChatWith(RegexName)` 模糊匹配，"Liuying" 匹配到 "Liuying、』婉~一璐相伴』" | 移除 ChatWith，改用 `ListItemControl(Name=).Click()` 精确完整名称 |
| 重复回复同一消息 | 3 个 Python 进程并行（删 pid 前旧进程未杀） | 文档警告 "禁止手动删 bridge.pid" |
| 同消息反复回复 | False positive unread | `PER_SESSION_REPLY_COOLDOWN=30s` + Self 消息过滤 + 指纹含 sender |
| 回复发到错误会话 | 用 `who` (GetSessionList key) 而非实际打开的 `chat_name` 发送 | 统一用 `chat_name` 发送回复 |
| 搜索框 ENTER 串群 | 微信搜索即时过滤，按 ENTER 会打开**第一个搜索结果** | 移除搜索中的 `{ENTER}`，改用 RegexName 侧边栏精确匹配 |

### 第二轮（2026-05-13/14）— 时序漂移修复

| 问题 | 根因 | 修复 |
|------|------|------|
| 会话时序漂移 | UIA 标题切换有延迟 | `_wait_chat_stable()` 连续两次标题匹配 + GetAllMessage 后回校验 |
| Hermes 调用阻塞 | 默认无超时，知识库检索可无限等待 | `HERMES_TIMEOUT=120s` + `socket.setdefaulttimeout()` |
| @提及过滤 | 群聊中 @他人 的消息不该回复 | `re.findall(r'@\S+')` 过滤 @非 bot 昵称 |
| 跨午夜停摆 | 活跃时间用 `start <= hour < end` | 改为 `hour >= start OR hour < end` |

### 第三轮（2026-05-15）— 窗口焦点与未读计数

| 问题 | 根因 | 修复 |
|------|------|------|
| 无消息时抢夺微信窗口 | 每 3s 无条件 `A_ChatIcon.DoubleClick()` + `SwitchToThisWindow()` | 先直接 GetSessionList，30s 空结果才 DoubleClick；移除 SwitchToThisWindow |
| 回复后看不到新消息 | `_last_session_counts` 用原始 sessions 覆盖，无视清零 | Phase3 成功后 `_last_session_counts[who]=0`，合并时保留清零 |
| 私聊不回复 | 待定（见 Bug 1） | 待修复 |

---

## 四、当前未解决问题

### P0: 私聊消息路由错误（Bug 1）

bridge 只检测到群聊 "测试自动回复" 的未读消息，不检测私聊 "Liuying" 的未读消息。

**下一步**: 需要在 `GetSessionList` 返回后打印完整结果（不只是有变化的），并在 `_open_session` 的每个分支打印详细信息，定位是 A）GetSessionList 没返回，还是 B）打开了错误的会话。

### P1: 启动清积压后仍然回复积压消息（Bug 4）

**下一步**: prime 完成后保存 `_last_session_counts` 基线。

### P2: 30s 懒刷新仍会在无消息时抢焦点（Bug 3 的副作用）

每 30s 一次的 DoubleClick 仍然会激活微信窗口。

---

## 五、架构反思

### 反复修复同一类问题的根因

1. **微信 UIA 树不可靠**: `GetSessionList`、`ChatBox.TextControl.Name`、`ButtonControl.Name` 的行为在不同 WeChat 版本、不同 UIA 状态下不一致。bridge 需要过多防御性代码来应对。

2. **状态同步没有闭环**: bridge 维护了自己的状态（`_last_session_counts`、`seen`、`SessionStore`），但**无法直接读取 WeChat 的真实状态**，只能通过 UIA 间接推断。状态不一致时就会出错。

3. **Phased 架构的脆弱性**: Phase1→Phase2→Phase3 分离了采集/推理/投递，但 Phase1 和 Phase3 之间微信窗口状态可能漂移。中间 Hermes 推理可能耗时数十秒，窗口状态已完全改变。

4. **窗口操作必然抢焦点**: 任何 UIA 操作（Click、DoubleClick、SendKeys）都可能激活窗口。要做到完全无感，需要微信 API 而非 UIA。UIA 本质上是模拟用户操作，无法完全透明。

### 建议的长期方向

1. 优先使用 Hook/API 方式检测新消息，而非轮询 UIA
2. 如果必须用 UIA，减少操作频率（当前 3s 太激进）
3. 增加状态快照 + 校验闭环（如每轮操作前/后记录窗口状态）
4. 考虑只用 UIA 做消息采集+投递，用 WeChat Web/API 做消息检测
