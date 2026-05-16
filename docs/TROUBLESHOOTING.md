# wxauto Bridge 问题分析与修复历史

> 最后更新：2026-05-16

## 零、C_MsgList RDP 依赖（2026-05-16 发现）

### 现象

RDP 断开后 bridge 失明：`Phase1: GetAllMessage 返回空` 对所有会话持续出现。

### 根因

WeChat 3.9.11.25 在桌面会话非活跃（RDP 断开、锁屏）时，**ChatBox UIA 树中不再暴露 `C_MsgList` (ListControl Name='消息')**。`GetAllMessage()` 静默返回 `[]`。

**证据**：
- RDP 连接时（22:39-23:46）：`GetAllMessage 返回 42 条` — C_MsgList 存在
- RDP 断开后（次日 09:14+）：`GetAllMessage 返回空` 持续 — C_MsgList 消失
- 深度 UIA 扫描确认：ChatBox 仅 12 个控件（4 个工具栏按钮，无消息控件）
- SessionBox 始终暴露完整 UIA 数据：会话名、未读数、最后消息预览文本、时间戳

### 修复：SessionBox 预览兜底

当 `GetAllMessage()` 返回空时，bridge 从 SessionBox UIA 子树读取消息预览作为替代。

**实现**（`wxauto_bridge.py`，2026-05-16）：
1. 新增 `_read_sessionbox_preview(wx)` 函数，遍历 SessionBox 的 ListItemControl 子控件
2. 每个 ListItemControl 内嵌 `PaneControl → TextControl[]`，其中倒数第二个 TextControl 为消息预览（格式：`SenderName：Content`）
3. Phase1 在 `GetAllMessage` 返回空时，从预读的 `_sessionbox_previews` 中查找并处理
4. 日志标记 `Phase1 预览采集` 区分正常采集

**SessionBox ListItemControl 结构**（WeChat 3.9.11）:
```
ListItemControl Name='测试自动回复3条新消息'
  PaneControl
    ButtonControl Name='会话名'
    TextControl '会话名'
    TextControl '时间戳'
    TextControl 'SenderName：Content'   ← 消息预览
    TextControl '未读数'
```

### 局限性

| 限制 | 说明 |
|------|------|
| 内容截断 | SessionBox 预览 ~20 字符，长消息不完整 |
| 无消息历史 | 只能读取最后一条，无法获取更早的未读消息 |
| Sender 解析可能失败 | 依赖 `：` / `:` 分隔符 |
| 预览更新延迟 | 微信可能延迟刷新 SessionBox 预览文本 |

---

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

### Bug 1: 会话路由错误 — 私聊/群聊混淆（关键发现，2026-05-15 19:00 确认）

**现象**: 私聊的消息，bridge 的回复出现在群聊中。

**决定性证据**（2026-05-15 19:00 日志）:

```
18:59:33  会话变化: {'Liuying': 1}                         ← 检测到未读
18:59:35  _open: RegexName 命中 'Liuying'                  ← 打开名为 Liuying 的会话
18:59:38  Phase1 采集 [Liuying] Liuying: 在干嘛呢            ← 发送者=Liuying
18:59:52  Phase3 已回复 [Liuying]: 您好 Liuying！           ← 回复走 chat_name='Liuying'

19:00:00  _open: RegexName 命中 'Liuying'                  ← 又打开 Liuying
19:00:04  Phase1 采集 [Liuying] Merry组织发展教练: 你是谁    ← 发送者变成"Merry组织发展教练"！
19:00:13  Phase3 已回复 [Liuying]: ...                     ← 回复走 chat_name='Liuying'

19:00:27  _open: RegexName 命中 'Liuying'
19:00:30  Phase1 采集 [Liuying] Merry组织发展教练: 我可以咨询健康问题吗
```

**关键线索**: bridge 始终打开名为 "Liuying" 的会话（RegexName 一致命中），但 Phase1 采集到的消息发送者时而 "Liuying" 时而 "Merry组织发展教练"。这说明微信 SessionBox 中存在**多个名字相同或包含 "Liuying" 的会话**（私聊、群聊、或一个以 "Liuying" 命名的群），RegexName 在不同 Poll 中匹配到了不同会话。

**历史同类问题**（2026-05-13）: `RegexName` 模糊匹配导致 `"Liuying"` 匹配到了 `"Liuying、』婉~一璐相伴』"`。当时通过 `^` 和 `$` 锚点修复为精确匹配。

**当前 RegexName pattern**:
```python
pattern = f'^{_re.escape(target_name)}(\\d+条新消息)?(已置顶)?$'
```

对于 `target_name = "Liuying"`，pattern 为 `^Liuying(\d+条新消息)?(已置顶)?$`。这个正则本身是正确的，但如果 WeChat SessionBox 中存在多个 ListItemControl 其 Name 以 "Liuying" 开头（如私聊 "Liuying" 和群聊 "Liuying" 的某个变体），`SessionBox.ListItemControl(RegexName=pattern)` 返回的是**第一个匹配项**，可能不是目标会话。

**根因**: **会话名称碰撞** — 微信中存在多个名称相同或名称以 "Liuying" 开头的会话（私聊 + 群聊），UIA RegexName 无明确语义区分。

**影响的会话**:
- 私聊 "Liuying"（bot 账户与 Liuying 的一对一会话）
- 疑似一个也名为 "Liuying" 的群聊（其中成员包括 "Merry组织发展教练"）
- 群聊 "测试自动回复"（其中成员包括 Liuying）

**方向**: 需要区分同名会话。可尝试：
1. 在 `_open_session` 的 sibling 遍历中检查会话类型（私聊 vs 群聊的 UIA 属性差异）
2. 在 `GetSessionAmont` 阶段就获取会话类型信息
3. 利用 WeChat 窗口标题栏区分私聊（显示对方昵称）vs 群聊（显示群名）

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

### 第四轮（2026-05-15 晚间）— 排障测试

| 实验 | 结论 |
|------|------|
| 移除所有风控（can_reply / can_reply_session / _last_session_counts / MAX_SESSIONS_PER_CYCLE / _last_empty_read） | **无效** — 私聊仍然不回复，风控不是原因 |
| 检查 wxauto 未使用文件（color.py / errors.py / languages.py） | **无关** — color.py 和 errors.py 完全未被引用；languages.py 由 wxauto.py line 8 `from .languages import *` 导入并使用，中文微信下正常工作 |

**最终定位**（19:00 日志）: 风控移除后 bridge 能检测到 "Liuying" 的未读，也成功打开会话，但打开的会话是**群聊而非私聊**。Bug 1 根因确认为**同名会话 RegexName 碰撞**。

---

## 四、当前未解决问题

### P0 (已解决): RDP 断开后 GetAllMessage 返回空

**2026-05-16 已修复**: 新增 `_read_sessionbox_preview()` 函数，从 SessionBox UIA 子树读取消息预览作为兜底。详见「零、C_MsgList RDP 依赖」章节。

### P0: 同名会话 RegexName 碰撞（Bug 1，已定位）

微信 SessionBox 中存在多个名称包含 "Liuying" 的会话（私聊 + 群聊），`RegexName` 返回第一个匹配的 ListItemControl，可能是群聊而非私聊，导致所有回复发到群里。

**下一步**: 需要区分同名会话的类型（私聊 vs 群聊）。方向：
1. 利用 UIA 属性区分（群聊的 ListItemControl 可能有头像/成员数等不同特征）
2. 利用 `GetSessionAmont` 阶段获取额外区分信息
3. 利用窗口标题栏在打开后做二次校验：私聊标题=对方昵称，群聊标题=群名

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
