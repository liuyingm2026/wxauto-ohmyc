# wxauto_bridge 守护进程策划

## 一、服务全景图

```
┌─── Windows ───────────────────────────────────────────────┐
│                                                            │
│  ③ WeChat PC (GUI, 必须手动登录)                            │
│      ↕ UIAutomation                                        │
│  ④ wxauto_bridge.py ←── http :8647 ──┐                    │
│      (watchdog 守护)                  │                    │
│                                       │                    │
├─── WSL2 Ubuntu-24.04 ─────────────────┤───────────────────┤
│                                       │                    │
│  ② Hermes qljk gateway ──────────────┘                    │
│  ② Hermes landlord gateway                                │
│  ② Hermes default gateway                                 │
│  ① FRP clients (landlord + qljk)                          │
│  ① wechat_callback.py (landlord :8645)                    │
│  ① wecom_callback.py (qljk :8648)                          │
│                                                            │
└────────────────────────────────────────────────────────────┘

启动顺序: ① → ② → ③ → ④
          FRP/回调 → Gateway → 微信登录 → 桥接
```

## 二、服务清单与故障模式

| # | 服务 | 位置 | 端口 | 启动方式 | 故障后果 |
|---|------|------|------|---------|---------|
| 1 | FRP Landlord | WSL | — | nohup + frpc | 公众号收不到消息 |
| 2 | FRP Qljk | WSL | — | nohup + frpc | 企微收不到消息 |
| 3 | wechat_callback | WSL | 8645 | nohup + python3 | 公众号消息丢失 |
| 4 | wecom_callback | WSL | 8648 | nohup + python3 | 企微消息丢失 |
| 5 | Hermes landlord | WSL | 8648/8649 | hermes gateway run | 公众号 AI 不可用 |
| 6 | **Hermes qljk** | WSL | 8647/8646 | hermes gateway run | **桥接 AI 不可用** |
| 7 | Hermes default | WSL | 8643/8644 | hermes gateway run | 飞书 default bot 不可用 |
| 8 | **WeChat PC** | Windows | — | 手动启动+扫码 | **桥接无法操控微信** |
| 9 | **wxauto_bridge** | Windows | — | python 脚本 | **微信消息无人回复** |

## 三、故障场景与恢复策略

### 场景 A：Windows 重启
**影响**: 全部服务停止
**恢复**:
1. WSL2 随 Windows 自动启动（已配置 `systemd=true`）
2. ⚠️ WSL 内服务不会自动启动（当前使用 nohup，非 systemd）
3. ⚠️ WeChat PC 需要手动登录
4. ⚠️ wxauto_bridge 需要手动启动

**理想恢复链**:
```
Windows boot
  → WSL2 启动 (自动)
    → systemd 自动启动 7 个 Hermes 服务 (需改造)
  → 用户手动登录微信
  → watchdog 检测到微信+Hermes 均在线 → 启动 bridge
```

### 场景 B：Hermes qljk 进程崩溃
**影响**: bridge API 调用失败，消息不回复
**恢复**:
1. bridge 检测到 Hermes 不可达（socket timeout 120s）
2. 日志记录错误，消息指纹不保存（下轮重试）
3. ⚠️ 当前 bridge **不会**自动重启 Hermes
4. 需要外部 watchdog 检测 :8647 端口 → 重启 qljk gateway

### 场景 C：wxauto_bridge 崩溃
**影响**: 微信消息无人回复
**恢复**:
1. watchdog 检测 bridge 进程不存在 → 重启
2. bridge 启动时 `prime_startup` 清积压
3. 恢复处理

### 场景 D：WeChat PC 崩溃/登出
**影响**: bridge 无法操控微信
**恢复**:
1. bridge `GetSessionList` 等操作抛异常
2. 连续错误 >10 次 → bridge 退出
3. watchdog 重启 bridge → 再次失败 → 循环
4. ⚠️ **需要人工恢复**：重新登录微信

### 场景 E：WSL2 崩溃
**影响**: 所有 WSL 内服务停止
**恢复**:
1. bridge 检测到 Hermes 不可达
2. ⚠️ 需要重启 WSL 和服务

## 四、守护方案设计

### 4.1 方案对比

| 方案 | 复杂度 | 可靠性 | 说明 |
|------|--------|--------|------|
| A. 简单 .bat 循环 | 低 | 中 | `:loop` + `start python` + `timeout` |
| B. Windows 计划任务 | 中 | 高 | 每分钟检查进程，死则重启 |
| C. NSSM Windows 服务 | 高 | 高 | 注册为系统服务，自动重启 |
| D. 多层 watchdog | 高 | 最高 | Windows watchdog + WSL watchdog |

### 4.2 推荐：方案 B + WSL systemd（渐进式）

#### 第一层：WSL side — systemd 化

将 7 个 WSL 服务从 `nohup` 改为 systemd unit 文件：

```
~/.config/systemd/user/hermes-qljk.service
~/.config/systemd/user/hermes-landlord.service
~/.config/systemd/user/hermes-default.service
~/.config/systemd/user/frpc-landlord.service
~/.config/systemd/user/frpc-qljk.service
~/.config/systemd/user/wechat-callback.service
~/.config/systemd/user/wecom-callback.service
```

每个 unit 配置 `Restart=always` + `RestartSec=10s`。

**优点**: WSL 重启后自动启动所有服务，进程崩溃自动恢复

**缺点**: 需要创建 7 个 systemd unit，且需要 `lingering` 使 user systemd 在登出后保持运行：
```bash
sudo loginctl enable-linger ohmyc
```

#### 第二层：Windows side — 健康检查 + 自动重启脚本

一个 PowerShell 脚本 `watchdog.ps1`，每分钟执行：

```
1. 检查 wxauto_bridge 进程是否存在
   → 不存在: 检查前置条件（微信 + Hermes :8647）
     → 都满足: 启动 bridge
     → 不满足: 记录日志，等待下次检查

2. 检查 Hermes :8647 是否可达
   → 不可达: 尝试通过 WSL 重启 qljk gateway
     (wsl -d Ubuntu-24.04 -- bash -c "systemctl --user restart hermes-qljk")

3. 记录健康状态到 watchdog.log
```

通过 Windows 计划任务触发：每分钟运行一次 + 系统启动时运行。

#### 第三层：Bridge 自身韧性增强

| 增强点 | 当前行为 | 改进后 |
|--------|---------|--------|
| Hermes 启动检查 | 不可达直接 `sys.exit(1)` | 重试 3 次，每次间隔 10s |
| Hermes 调用失败 | 标记重试（但红点已消） | 已有（非致命错误不退出） |
| 连续 UIA 错误 | >10 次退出 | 保持不变，让 watchdog 重启 |
| 微信断连检测 | 无 | 定期检查 `wx.nickname` 是否可读 |

### 4.3 实施文件清单

```
wxauto_bridge/
├── watchdog.ps1              # Windows 守护脚本（健康检查+自动重启）
├── setup_watchdog.bat        # 一键安装计划任务（管理员运行）
├── wsl/
│   ├── hermes-qljk.service        # qljk gateway systemd unit
│   ├── hermes-landlord.service    # landlord gateway
│   ├── hermes-default.service     # default gateway
│   ├── frpc-landlord.service
│   ├── frpc-qljk.service
│   ├── wechat-callback.service
│   ├── wecom-callback.service
│   └── install-services.sh        # 一键安装所有 systemd unit
├── README.md                 # 项目文档
├── HANDOVER.md               # (更新)
└── docs/
    └── DAEMON_PLAN.md        # 本文件
```

### 4.4 实施步骤建议

**Phase 1: WSL 服务 systemd 化**（最优先）
1. 创建 7 个 systemd user unit 文件
2. `loginctl enable-linger` 保持 user session
3. 安装、启动、验证
4. 重启 WSL 测试自动恢复

**Phase 2: watchdog.ps1**
1. 编写健康检查逻辑
2. 编写自动重启逻辑
3. 手动测试

**Phase 3: Windows 计划任务**
1. 创建计划任务 XML / 或通过 PowerShell 注册
2. 配置触发条件（每分钟 + 系统启动）
3. 验证

**Phase 4: Bridge 韧性增强**
1. Hermes 启动检查从 `sys.exit` 改为重试
2. 加微信断连检测

## 五、当前阻塞问题

| # | 问题 | 影响 |
|---|------|------|
| 1 | WSL 服务全部 nohup，重启 WSL 后丢失 | Windows 重启后 Hermes 全挂 |
| 2 | 微信需要手动登录 | 无人值守不可行 |
| 3 | bridge `sys.exit(1)` 在 Hermes 启动检查中 | 需 watchdog 重启 |

## 六、建议的决策点

1. **微信手动登录**：这是不可绕过的限制。微信 PC 需要扫码，无法自动化。
   → 接受此限制，bridge/守护只在微信已登录时工作
   → watchdog 检测微信不在时记录告警但不退出

2. **WSL systemd vs nohup**：systemd 更可靠但设置复杂。
   → 建议先用 systemd 化（一劳永逸）
   → 或保持 nohup + watchdog 通过 WSL 命令重启

3. **watchdog 粒度**：每分钟检查 vs 每 10 秒？
   → 每分钟足够（bridge 轮询也是 3s 间隔）
   → 太频繁消耗资源
