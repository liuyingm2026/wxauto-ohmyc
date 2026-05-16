# wxauto_bridge — 微信 → Hermes qljk AI 桥接

用 wxauto (UIAutomation) 操控 Windows 微信 PC 客户端，轮询未读消息 → 调用 WSL2 内 Hermes qljk Agent API → 发送 AI 回复。

```
WeChat PC (Windows) → wxauto → bridge.py → HTTP :8647 → WSL2 Hermes qljk
```

## 前置条件

1. **微信 PC 客户端** 3.9.x 已登录（必须手动扫码，无法自动化）
2. **WSL2** 内 Hermes qljk gateway 运行中: `hermes gateway run --profile qljk`
3. **Python 3.12**: `C:\Program Files\Python312\python.exe`
4. **wxauto**: `pip install wxauto`

## 快速启动

```bash
# 方式一：双击
start_bridge.bat

# 方式二：命令行
"C:\Program Files\Python312\python.exe" C:\Users\ohmyc\.openclaw\workspace\wxauto_bridge\wxauto_bridge.py
```

## 停止

- 终端窗口按 `Ctrl+C`（推荐，会保存状态）
- `taskkill /PID <pid>`（查看 `data\bridge.pid` 获取 PID）
- **不要手动删除 `bridge.pid`** — 必须先杀进程再删文件，否则会导致多实例并行重复回复

## 核心机制

| 机制 | 说明 |
|------|------|
| PID 文件锁 | 防止多实例互抢未读红点 |
| 指纹去重 | `sha256(chat_name\|sender\|content)` 防重复处理 |
| 启动预热 | `prime_startup_messages()` 遍历未读标记已读不回复 |
| 会话冷却 | 回复后 30s 内不再回复同一会话 |
| 只回复最新 | 每轮每会话只处理最新一条未读 |
| @提及过滤 | @了非 bot 昵称的人 → 标记已读不回复 |
| 白名单/黑名单 | `ALLOW_SESSIONS` 空=回复所有，`SKIP_SESSIONS` 跳过 |
| 跨午夜活跃时间 | 7:00-3:00（凌晨），使用 OR 逻辑 |
| 窗口稳定检测 | 连续两次 UIA 标题匹配才确认切换完成 |

## 风控参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `REPLY_DELAY_MIN/MAX` | 0.5-2s | 收到消息后随机等待 |
| `DAILY_TOTAL_LIMIT` | 200 | 全天回复上限 |
| `PER_USER_HOURLY_LIMIT` | 20 | 单用户每小时上限 |
| `MAX_SESSIONS_PER_CYCLE` | 3 | 每轮最多处理会话数 |
| `HERMES_TIMEOUT` | 120s | socket 级强制超时 |
| `POLL_INTERVAL` | 3±2s | 带随机抖动 |

## 文件结构

```
wxauto_bridge/
├── wxauto_bridge.py          # 主脚本
├── start_bridge.bat           # 启动批处理
├── README.md                  # 本文件
├── HANDOVER.md                # 踩坑记录与修复历史
├── docs/
│   └── DAEMON_PLAN.md         # 守护进程策划
└── data/
    ├── bridge.log             # 运行日志
    ├── conversation_history.json  # 会话上下文
    ├── seen_messages.json     # 已处理消息指纹
    ├── bridge_state.json      # 日回复计数器
    └── bridge.pid             # 进程锁
```

## 监控与日志

```bash
# 查看实时日志
tail -f C:\Users\ohmyc\.openclaw\workspace\wxauto_bridge\data\bridge.log

# 检查进程是否存活
tasklist | findstr python

# 查看今日回复数
type C:\Users\ohmyc\.openclaw\workspace\wxauto_bridge\data\bridge_state.json
```

## 重置测试环境

1. `taskkill /F /PID <pid>` 停 bridge
2. `tasklist | findstr python` 确认无残留
3. 清空 `data/conversation_history.json` → `{"sessions": {}, "last_active": {}}`
4. 清空 `data/seen_messages.json` → `[]`
5. 清空 `data/bridge_state.json` → `{"daily_total": 0, "daily_date": "2026-05-13"}`
6. 删除 `data/bridge.pid`
7. 微信中手动清除各聊天记录
8. 重启 bridge

## 依赖

- Python 3.12 + wxauto (pywin32, comtypes)
- WSL2 Ubuntu-24.04 + Hermes qljk gateway
- 微信 PC 客户端 3.9.x

## 更多

- 完整踩坑记录和修复历史见 [HANDOVER.md](HANDOVER.md)
- 守护进程策划见 [docs/DAEMON_PLAN.md](docs/DAEMON_PLAN.md)
