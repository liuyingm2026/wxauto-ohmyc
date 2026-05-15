#!/usr/bin/env python3
"""
wxauto → Hermes qljk Agent 桥接脚本

用 wxauto (UIAutomation) 操控 Windows 微信客户端，作为桥接接入 Hermes qljk Agent。
替代企业微信自建应用方案，支持私聊和群聊。

架构: WeChat PC → wxauto → wxauto_bridge.py → HTTP → WSL2 Hermes qljk (:8647)

不影响任何现有 Hermes agent 和 channel 配置。
"""

import json
import logging
import os
import random
import re
import signal
import socket
import sys
import threading
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# ── 风控配置 ──────────────────────────────────────────────────
# 所有参数可调，数值越保守越不容易触发微信风控
# ═══════════════════════════════════════════════════════════════

# ── 连接配置
HERMES_API_URL = "http://127.0.0.1:8647/v1/chat/completions"

# ── 回复延迟（模拟人类思考/打字） ─────────────────────────
REPLY_DELAY_MIN = 0.5        # 收到消息后最小等待秒数（Hermes 耗时已是天然延迟）
REPLY_DELAY_MAX = 2.0        # 收到消息后最大等待秒数

# ── 频率限制 ──────────────────────────────────────────────
PER_USER_HOURLY_LIMIT = 20   # 同一用户每小时最多回复条数
DAILY_TOTAL_LIMIT = 200      # 全天总计最多回复条数
INTER_REPLY_COOLDOWN = 0.0   # 不启用（随机延迟 3-8s 已控制节奏）

# ── 会话切换限制 ──────────────────────────────────────────
SESSION_SWITCH_COOLDOWN = 5.0    # 切换到新会话后最短停留时间（秒）
MAX_SESSIONS_PER_CYCLE = 3        # 每轮最多处理几个会话
PER_SESSION_REPLY_COOLDOWN = 30   # 同一会话回复后冷却时间（秒），防止 false positive unread 导致重复回复

# ── Hermes 调用控制 ──────────────────────────────────────
HERMES_TIMEOUT = 120               # Hermes API 超时（秒），够知识库检索但不会无限阻塞
HERMES_LONG_CALL_THRESHOLD = 60    # Hermes 耗时超此值时，处理下个会话前刷新 GetSessionList
socket.setdefaulttimeout(HERMES_TIMEOUT)  # OS 级强制超时，覆盖 connect + read

# ── 轮询配置 ──────────────────────────────────────────────
POLL_INTERVAL = 3                # 基础轮询间隔（秒）
POLL_JITTER = 2.0                # 轮询随机抖动范围 ±（秒）

# ── 活跃时间（24h格式，UTC+8） ────────────────────────────
ACTIVE_START_HOUR = 7            # 早 7 点开始回复
ACTIVE_END_HOUR = 3              # 凌晨 3 点停止回复（跨午夜，start > end 时用 OR 逻辑）

# ── 消息限制 ──────────────────────────────────────────────
MAX_REPLY_LENGTH = 2000          # 微信单条消息上限
REPLY_CHUNK_DELAY = 1.5          # 长消息分段发送间隔（秒）

# ── 会话上下文 ──────────────────────────────────────────
MAX_HISTORY_TURNS = 10           # 每个会话保留最近 N 轮对话（一问一答 = 1 轮）
SESSION_TTL_SECONDS = 1800       # 会话空闲 30 分钟后自动归档（秒）

# ── 窗口控制 ──────────────────────────────────────────────
MINIMIZE_ON_START = False        # 不能最小化！wxauto SendMsg 需要窗口存在
AVOID_FOREGROUND = True          # 避免强制把微信拉到前台

# ── 白名单（只回复这些会话，空集合=回复所有非黑名单会话） ──
ALLOW_SESSIONS = set()  # 空=所有会话

# ── 黑名单（不回复的会话） ────────────────────────────────
SKIP_SESSIONS = {
    "微信团队", "WeChat Team", "QQ邮箱提醒", "腾讯新闻",
    "微信支付", "微信运动", "微信游戏",
}

# ═══════════════════════════════════════════════════════════════
# ── 微信 UI 后缀清洗 ─────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════
# ListItemControl.Name 含 "X条新消息"/"已置顶" 等 UI 渲染后缀，
# ButtonControl.Name 在部分 WeChat 版本为泛型 "SessionListItem"。
# wxauto GetSessionList 返回的键名是已清洗的纯净名，需一致比对。
_BADGE_PATTERNS = [
    r'\d+条新消息$',    # "3条新消息"
    r'已置顶$',          # "已置顶"
    r'\]\s*$',           # 末尾 "] " (群聊成员提示)
]

def _strip_badges(name):
    """从 ListItemControl.Name 剔除已知 UI 标记后缀，返回纯净会话名。"""
    result = name
    for pat in _BADGE_PATTERNS:
        result = re.sub(pat, '', result).rstrip()
    return result

# ═══════════════════════════════════════════════════════════════
# ── 运行时状态文件 ───────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
SEEN_MSG_FILE = DATA_DIR / "seen_messages.json"
STATE_FILE = DATA_DIR / "bridge_state.json"
PID_FILE = DATA_DIR / "bridge.pid"

logging.basicConfig(
    level=logging.INFO,  # root logger stays INFO to silence wxauto DEBUG spam
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(DATA_DIR / "bridge.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("wxauto-bridge")
logger.setLevel(logging.DEBUG)  # bridge debug messages visible

# ── 崩溃捕获：未处理异常写入日志 ──────────────────────
def _log_unhandled_exception(exc_type, exc_value, exc_tb):
    import traceback
    tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    logger.critical("未处理异常崩溃:\n%s", "".join(tb_lines))
    # 也写一份到单独的崩溃日志
    crash_file = DATA_DIR / "crash.log"
    try:
        with open(crash_file, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now().isoformat()} ===\n")
            f.write("".join(tb_lines))
    except Exception:
        pass
sys.excepthook = _log_unhandled_exception

# ═══════════════════════════════════════════════════════════════
# ── 运行时计数器 ─────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

class RateLimiter:
    """频率限制器：跟踪每用户 + 全局发送计数"""

    def __init__(self):
        self.user_hourly: dict[str, list[float]] = defaultdict(list)
        self.daily_total = 0
        self.daily_date = datetime.now().date()
        self.last_reply_at = 0.0
        self.last_session_switch_at = 0.0
        self.last_reply_per_session: dict[str, float] = {}  # 会话级回复冷却
        self._load_state()

    def _load_state(self):
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self.daily_total = data.get("daily_total", 0)
                saved_date = data.get("daily_date", "")
                if saved_date != str(datetime.now().date()):
                    self.daily_total = 0
        except Exception:
            pass

    def _save_state(self):
        try:
            STATE_FILE.write_text(json.dumps({
                "daily_total": self.daily_total,
                "daily_date": str(self.daily_date),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _reset_daily_if_needed(self):
        today = datetime.now().date()
        if today != self.daily_date:
            self.daily_total = 0
            self.daily_date = today
            self.user_hourly.clear()

    def _prune_user_hourly(self, user: str):
        """清理用户一小时前的记录"""
        now = time.time()
        self.user_hourly[user] = [
            t for t in self.user_hourly[user] if now - t < 3600
        ]

    def can_reply(self, user: str) -> bool:
        """检查是否可以回复该用户"""
        self._reset_daily_if_needed()
        self._prune_user_hourly(user)

        # 全局日上限
        if self.daily_total >= DAILY_TOTAL_LIMIT:
            logger.warning("达到每日总回复上限 (%d)", DAILY_TOTAL_LIMIT)
            return False

        # 单用户小时上限
        if len(self.user_hourly[user]) >= PER_USER_HOURLY_LIMIT:
            logger.warning("用户 %s 达到每小时上限 (%d)", user[:20], PER_USER_HOURLY_LIMIT)
            return False

        # 回复间隔
        elapsed = time.time() - self.last_reply_at
        if elapsed < INTER_REPLY_COOLDOWN:
            logger.debug("回复冷却中 (还需 %.1f 秒)", INTER_REPLY_COOLDOWN - elapsed)
            return False

        return True

    def record_reply(self, user: str):
        """记录一次回复"""
        now = time.time()
        self.user_hourly[user].append(now)
        self.daily_total += 1
        self.last_reply_at = now
        self.last_reply_per_session[user] = now
        self._save_state()

    def can_reply_session(self, session: str) -> bool:
        """检查会话级冷却：防止 false positive unread 导致重复回复"""
        last = self.last_reply_per_session.get(session, 0)
        return time.time() - last >= PER_SESSION_REPLY_COOLDOWN

    def can_switch_session(self) -> bool:
        """检查是否可以切换会话"""
        elapsed = time.time() - self.last_session_switch_at
        return elapsed >= SESSION_SWITCH_COOLDOWN

    def record_session_switch(self):
        """记录会话切换"""
        self.last_session_switch_at = time.time()

    def is_active_hours(self) -> bool:
        """检查当前是否在活跃时段（支持跨午夜，如 7-3）"""
        now = datetime.now()
        if ACTIVE_START_HOUR < ACTIVE_END_HOUR:
            return ACTIVE_START_HOUR <= now.hour < ACTIVE_END_HOUR
        else:
            # 跨午夜: 如 7:00 - 03:00 → hour >= 7 OR hour < 3
            return now.hour >= ACTIVE_START_HOUR or now.hour < ACTIVE_END_HOUR

    def get_stats(self) -> dict:
        """获取当前统计信息"""
        self._reset_daily_if_needed()
        return {
            "daily_total": self.daily_total,
            "daily_limit": DAILY_TOTAL_LIMIT,
            "active_users": len(self.user_hourly),
        }


# ═══════════════════════════════════════════════════════════════
# ── 会话上下文管理 ───────────────────────────────────────────
# Hermes 忽略客户端传入的 session_id，每次 API 调用创建新会话。
# 因此桥接需要在本地维护每个微信会话的对话历史，注入到请求中。
# ═══════════════════════════════════════════════════════════════

SESSION_STORE_FILE = DATA_DIR / "conversation_history.json"

class SessionStore:
    """为每个微信会话维护独立的对话历史"""

    def __init__(self):
        self.sessions: dict[str, list[dict]] = defaultdict(list)  # who → [{"role","content"}, ...]
        self.last_active: dict[str, float] = {}  # who → last_timestamp
        self._load()

    def _load(self):
        try:
            if SESSION_STORE_FILE.exists():
                data = json.loads(SESSION_STORE_FILE.read_text(encoding="utf-8"))
                for who, msgs in data.get("sessions", {}).items():
                    # 只加载最近的 MAX_HISTORY_TURNS*2 条消息
                    self.sessions[who] = msgs[-(MAX_HISTORY_TURNS * 2):]
                for who, ts in data.get("last_active", {}).items():
                    self.last_active[who] = ts
                logger.info("已加载 %d 个会话历史", len(self.sessions))
        except Exception:
            pass

    def _save(self):
        try:
            SESSION_STORE_FILE.write_text(json.dumps({
                "sessions": {who: msgs[-(MAX_HISTORY_TURNS * 2):] for who, msgs in self.sessions.items() if msgs},
                "last_active": self.last_active,
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _prune_stale(self):
        """清理过期会话"""
        now = time.time()
        stale = [
            who for who, ts in self.last_active.items()
            if now - ts > SESSION_TTL_SECONDS
        ]
        for who in stale:
            if who in self.sessions:
                logger.info("会话过期归档: %s (%d 条消息)", who[:20], len(self.sessions[who]))
                del self.sessions[who]
            del self.last_active[who]
        if stale:
            self._save()

    def get_history(self, who: str) -> list[dict]:
        """获取某个会话的对话历史"""
        self._prune_stale()
        history = self.sessions.get(who, [])
        # 只返回最近 N 轮（每轮 = user + assistant）
        return history[-(MAX_HISTORY_TURNS * 2):]

    def add_user_message(self, who: str, content: str):
        """记录用户消息"""
        self.sessions[who].append({"role": "user", "content": content})
        self._trim(who)

    def add_assistant_message(self, who: str, content: str):
        """记录助手回复"""
        self.sessions[who].append({"role": "assistant", "content": content})
        self.last_active[who] = time.time()
        self._trim(who)
        self._save()

    def _trim(self, who: str):
        """裁剪到 MAX_HISTORY_TURNS 轮"""
        limit = MAX_HISTORY_TURNS * 2
        if len(self.sessions[who]) > limit:
            self.sessions[who] = self.sessions[who][-limit:]

# ═══════════════════════════════════════════════════════════════
# ── 消息指纹去重 ─────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

def load_seen():
    try:
        if SEEN_MSG_FILE.exists():
            return set(json.loads(SEEN_MSG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return set()

def save_seen(seen):
    try:
        items = list(seen)[-500:]
        SEEN_MSG_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def msg_fingerprint(who, sender, content):
    """消息指纹，含日期避免同内容跨天被永久去重。"""
    today = datetime.now().strftime('%Y%m%d')
    return sha256(f"{who}|{sender}|{content}|{today}".encode()).hexdigest()[:16]

# ═══════════════════════════════════════════════════════════════
# ── Hermes API 调用 ──────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

def call_hermes(session_id, user_name, message, history=None):
    """调用 Hermes API，可选注入对话历史"""
    now = datetime.now()
    date_context = (
        f"当前时间: {now.strftime('%Y年%m月%d日 %H:%M')} (星期{['一','二','三','四','五','六','日'][now.weekday()]})"
    )
    messages = list(history) if history else []
    messages.append({"role": "user", "content": f"[来自微信用户 {user_name}]\n{date_context}\n\n{message}"})

    payload = json.dumps({
        "model": "qljk",
        "messages": messages,
        "session_id": session_id,
        "stream": False,
    }, ensure_ascii=False).encode()

    req = urllib.request.Request(
        HERMES_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer local",
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            elapsed = time.time() - t0
            for choice in data.get("choices", []):
                msg = choice.get("message", {})
                reply = msg.get("content", "")
                if reply.strip():
                    return reply.strip(), elapsed
        return None, elapsed
    except Exception as e:
        elapsed = time.time() - t0
        logger.error("Hermes 调用失败 (%.1fs): %s", elapsed, e)
        return None, elapsed

# ═══════════════════════════════════════════════════════════════
# ── 微信窗口控制 ─────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

def minimize_wechat(wx):
    """最小化微信窗口"""
    try:
        import win32gui
        hwnd = getattr(wx, 'HWND', None)
        if hwnd:
            win32gui.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            logger.info("微信窗口已最小化")
    except Exception:
        pass


def _ensure_foreground(wx):
    """临时将微信窗口带到前台，确保 SendKeys 正确送达。

    SendKeys 发向当前焦点窗口。微信在后台时按键会被其他窗口拦截，导致
    搜索框输入无效、ESC 无法返回列表、ENTER 打开错误会话。
    调用者在需要键盘输入前调用此函数，操作完成后应尽快释放焦点。
    """
    try:
        import win32gui
        import win32con
        hwnd = getattr(wx, 'HWND', 0)
        if not hwnd:
            return False
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        return True
    except Exception:
        return False

# ═══════════════════════════════════════════════════════════════
# ── 消息发送 ─────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

def get_current_chat_name(wx):
    """读取当前微信聊天窗口标题栏名称（UIA TextControl）。"""
    try:
        name = wx.ChatBox.TextControl(searchDepth=15).Name
        return name.strip() if name else ""
    except Exception:
        return ""


def get_chat_name_win32(wx):
    """通过 Windows 窗口标题获取当前聊天名（win32gui 备用路径）。
    WeChat 3.9.x 标题格式为 "会话名"，点击不同会话后标题栏会切换。"""
    try:
        import win32gui
        hwnd = getattr(wx, 'HWND', 0)
        if not hwnd:
            return ""
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return ""
        title = title.strip()
        # WeChat 3.9.x 标题格式: "会话名"（当前聊天窗口的名称）
        # 主窗口无特定聊天时也可能是 "微信" 或其他
        if title == '微信':
            return ""
        return title
    except Exception:
        return ""


def verify_chat_window(wx, expected_name):
    """验证当前聊天窗口标题是否匹配目标会话名。不匹配说明串窗口了。
    同时尝试 UIA (ChatBox TextControl) 和 win32gui (GetWindowText) 两种路径，
    WeChat 3.9.12 中 UIA 控件树可能变化导致单一方法不可靠。"""
    current_uia = get_current_chat_name(wx)
    current_win32 = get_chat_name_win32(wx)

    def _matches(current):
        if not current:
            return False
        return expected_name in current or current in expected_name

    if _matches(current_uia):
        return True
    if _matches(current_win32):
        logger.debug("verify_chat: UIA='%s' 不匹配但 win32='%s' 匹配",
                     current_uia[:20], current_win32[:30])
        return True

    logger.warning("窗口验证失败: 期望=%s UIA=%s win32=%s",
                   expected_name[:20], current_uia[:30], current_win32[:30])
    return False


def wait_chat_stable(wx, target_name, max_wait=3.0, check_interval=0.3):
    """验证微信窗口标题已切换到目标会话，连续两次匹配才确认切换完成。
    同时检查 UIA 和 win32gui 两种路径，任一匹配即可。
    max_wait: 最长等待秒数，超时返回 False。"""
    deadline = time.time() + max_wait
    last_match = False
    while time.time() < deadline:
        uia_name = get_current_chat_name(wx)
        win32_name = get_chat_name_win32(wx)
        current = uia_name or win32_name
        if current and (target_name in current or current in target_name):
            if last_match:
                return True
            last_match = True
        else:
            last_match = False
        time.sleep(check_interval)
    logger.warning("wait_chat_stable 验证超时: 期望='%s' UIA='%s' win32='%s'",
                   target_name[:20],
                   get_current_chat_name(wx)[:30],
                   get_chat_name_win32(wx)[:30])
    return False


SEND_MSG_TIMEOUT = 30  # wx.SendMsg 单次调用最大等待时间（含 UIA COM 调用）


def _send_msg_with_timeout(wx, chunk, timeout=SEND_MSG_TIMEOUT):
    """在独立线程中调用 wx.SendMsg，超时则丢弃线程并返回 False。
    UIA COM 调用可能在 WeChat 繁忙时永久阻塞（COM 死锁），
    线程超时是 Windows 上唯一可靠的防护机制。"""
    result = [False]
    error = [None]
    barrier = threading.Barrier(2, timeout=timeout)

    def _worker():
        try:
            wx.SendMsg(chunk)
            result[0] = True
        except Exception as e:
            error[0] = e
        try:
            barrier.wait(1)
        except threading.BrokenBarrierError:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        barrier.wait()
    except threading.BrokenBarrierError:
        logger.error("wx.SendMsg 超时 %ds（COM 阻塞），放弃本轮发送", timeout)
        return False

    if error[0]:
        raise error[0]
    return result[0]


def send_reply(wx, who, reply):
    """分段发送回复消息。返回 True=成功。"""
    success = False
    while reply:
        chunk = reply[:MAX_REPLY_LENGTH]
        reply = reply[MAX_REPLY_LENGTH:]
        try:
            if _send_msg_with_timeout(wx, chunk):
                logger.info("已回复 %s (%d 字)", who[:20], len(chunk))
                success = True
            else:
                return False
        except Exception as e:
            logger.error("发送消息失败 to %s: %s", who[:20], e)
            return False
        if reply:
            time.sleep(REPLY_CHUNK_DELAY)
    return success

# ═══════════════════════════════════════════════════════════════
# ── 消息轮询与处理 ──────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

_last_sessions_str = ""  # 用于抑制重复的 GetSessionList 日志
_last_empty_read = {}     # 会话→时间戳：GetAllMessage 空读后冷却，防止无限循环
_last_session_counts = {} # 会话→未读数：上次 Poll 的快照，只在新消息增加时打开会话
_last_force_refresh = 0.0 # 上次强制返回聊天列表的时间戳
EMPTY_READ_COOLDOWN = 45  # 空读冷却秒数
FORCE_REFRESH_INTERVAL = 30  # 强制返回聊天列表的最小间隔（秒），避免频繁抢焦点

def prime_startup_messages(wx, seen):
    """启动时将现有未读消息全部标记为已读，不回复。
    解决长时间不登录后消息积压的问题——这些积压消息属于过去，不应自动回复。"""
    logger.info("启动清积压：扫描现有未读消息并标记已读...")
    try:
        sessions = wx.GetSessionList(newmessage=True)
    except Exception as e:
        logger.warning("清积压：获取会话列表失败: %s", e)
        return seen

    if not sessions:
        logger.info("清积压：没有未读消息，跳过")
        return seen

    primo_seen = set(seen)
    fingerprinted = 0
    sessions_cleared = 0

    for who, _ in sessions.items():
        if not who:
            continue
        if who in SKIP_SESSIONS:
            continue
        if ALLOW_SESSIONS and who not in ALLOW_SESSIONS:
            continue

        # 打开会话（搜索+侧边栏点击，不使用 ENTER 避免串群）
        try:
            import re as _re
            pattern = f'^{_re.escape(who)}(\\d+条新消息)?(已置顶)?$'

            # 先返回聊天列表
            wx.A_ChatIcon.DoubleClick(simulateMove=False)
            time.sleep(0.3)

            # 搜索过滤
            _ensure_foreground(wx)
            wx.B_Search.Click(simulateMove=False)
            time.sleep(0.15)
            wx.UiaAPI.SendKeys('{Ctrl}a{BACK}', waitTime=0.2)
            time.sleep(0.1)
            wx.B_Search.SendKeys(who, waitTime=1.0)
            time.sleep(0.8)

            # 侧边栏 RegexName 精确点击
            item = wx.SessionBox.ListItemControl(RegexName=pattern)
            if item.Exists(2.0):
                item.Click(simulateMove=False)
                time.sleep(0.5)
                if not wait_chat_stable(wx, who, max_wait=2.0):
                    logger.warning("清积压: 窗口验证失败 %s，跳过", who[:20])
                    wx.UiaAPI.SendKeys('{ESC}', waitTime=0.2)
                    time.sleep(0.2)
                    continue
            else:
                logger.warning("清积压: 搜索未找到 %s，跳过", who[:20])
                continue

            # 获取所有消息并指纹
            msgs = wx.GetAllMessage()
            for msg in reversed(msgs):
                msg_type = getattr(msg, 'type', '')
                if msg_type not in ('friend',):
                    continue
                content = getattr(msg, 'content', '') or ''
                sender = getattr(msg, 'sender', who)
                if not content or not isinstance(content, str) or not content.strip():
                    continue
                if content.startswith(('[图片]', '[文件]', '[语音]', '[Image]', '[File]', '[Voice]')):
                    continue
                if sender == 'Self':
                    continue
                fp = msg_fingerprint(who, sender, content.strip())
                primo_seen.add(fp)
                fingerprinted += 1

            sessions_cleared += 1
        except Exception as e:
            logger.warning("清积压：处理会话失败 %s: %s", who[:20], e)
            continue

    logger.info("清积压完成：%d 个会话，%d 条消息标记已读", sessions_cleared, fingerprinted)
    return primo_seen


def process_unread(wx, seen, rl: RateLimiter, ss: SessionStore):
    """处理所有带未读消息的会话，带完整风控保护"""
    global _last_sessions_str, _last_force_refresh, _last_session_counts
    try:
        # 先尝试直接获取（不触碰窗口，避免无消息时抢焦点）
        sessions = wx.GetSessionList(newmessage=True)
        # 如果返回空且距上次强制刷新超过间隔，可能是微信不在聊天列表
        # 此时做一次 DoubleClick 回到聊天列表后重试（频率受控，30s 最多一次）
        if not sessions and time.time() - _last_force_refresh > FORCE_REFRESH_INTERVAL:
            logger.debug("GetSessionList 持续返回空，尝试返回聊天列表")
            try:
                wx.A_ChatIcon.DoubleClick(simulateMove=False)
                time.sleep(0.3)
                _last_force_refresh = time.time()
            except Exception:
                pass
            sessions = wx.GetSessionList(newmessage=True)
        # 节流日志：只在会话列表变化时输出
        sessions_str = str({k: v for k, v in sorted(sessions.items())[:10]}) if sessions else ""
        if sessions and sessions_str != _last_sessions_str:
            logger.info("会话变化: %s", sessions_str)
            _last_sessions_str = sessions_str
        elif not sessions:
            _last_sessions_str = ""
    except Exception as e:
        logger.warning("获取会话列表失败: %s", e)
        return seen

    if not sessions:
        return seen

    updated_seen = set(seen)
    # ── 辅助函数（会话打开与窗口控制）──────────────────────
    def _get_session_name(item):
        try:
            btn = item.ButtonControl()
            if btn.Name == 'SessionListItem':
                return _strip_badges(item.Name)
            else:
                return btn.Name
        except Exception:
            return _strip_badges(item.Name) if item.Name else item.Name

    def _open_session(target_name):
        """打开微信会话（三步递进，每步验证窗口切换）。

        1. RegexName 侧边栏精确点击 → 验证窗口
        2. Sibling 遍历精确匹配 → 验证窗口
        3. 搜索过滤 + 侧边栏点击 → 验证窗口（不使用 ENTER）

        任一步验证通过即返回 True。三步全失败返回 False，
        调用方应跳过此会话。旧版永远返回 True 是串聊回复的根因。
        """
        import re as _re
        pattern = f'^{_re.escape(target_name)}(\\d+条新消息)?(已置顶)?$'

        # ── Step 0: 返回聊天列表 ──────────────────────────
        try:
            wx.A_ChatIcon.DoubleClick(simulateMove=False)
            time.sleep(0.5)
        except Exception:
            pass
        wx.UiaAPI.SendKeys('{ESC}', waitTime=0.2)
        time.sleep(0.1)
        wx.UiaAPI.SendKeys('{ESC}', waitTime=0.2)
        time.sleep(0.3)

        # ── Step 1: RegexName 精确匹配 → 验证 ─────────────
        try:
            item = wx.SessionBox.ListItemControl(RegexName=pattern)
            if item.Exists(2.0):
                logger.info("_open: RegexName 命中 '%s'", target_name[:20])
                item.Click(simulateMove=False)
                time.sleep(0.5)
                if _wait_chat_stable(target_name):
                    logger.info("_open: RegexName -> '%s'", target_name[:20])
                    return True
                logger.warning("_open: RegexName 点击后验证失败 '%s'", target_name[:20])
        except Exception:
            pass

        # ── Step 2: Sibling 遍历精确匹配 → 验证 ──────────
        item = wx.SessionBox.ListItemControl()
        for _ in range(80):
            try:
                if item is None:
                    break
                name = _get_session_name(item)
                if name == target_name:
                    logger.info("_open: sibling 匹配 '%s'", name[:25])
                    item.Click(simulateMove=False)
                    time.sleep(0.5)
                    if _wait_chat_stable(target_name):
                        logger.info("_open: sibling -> '%s'", target_name[:20])
                        return True
                    logger.warning("_open: sibling 验证失败 '%s'", target_name[:20])
                    break
                item = item.GetNextSiblingControl()
                if item is None:
                    break
            except Exception:
                break

        # ── Step 3: 搜索过滤 + 侧边栏点击（不使用 ENTER） ──
        try:
            _ensure_foreground(wx)
            wx.B_Search.Click(simulateMove=False)
        except Exception:
            pass
        time.sleep(0.15)
        wx.UiaAPI.SendKeys('{Ctrl}a{BACK}', waitTime=0.2)
        time.sleep(0.1)
        wx.B_Search.SendKeys(target_name, waitTime=1.0)
        time.sleep(0.8)

        # 搜索过滤后重新尝试 RegexName
        try:
            item = wx.SessionBox.ListItemControl(RegexName=pattern)
            if item.Exists(2.0):
                logger.info("_open: 搜索+RegexName 命中 '%s'", target_name[:20])
                item.Click(simulateMove=False)
                time.sleep(0.5)
                if _wait_chat_stable(target_name):
                    logger.info("_open: 搜索+RegexName -> '%s'", target_name[:20])
                    return True
        except Exception:
            pass

        # 搜索后 Sibling 遍历兜底
        item = wx.SessionBox.ListItemControl()
        for _ in range(80):
            try:
                if item is None:
                    break
                name = _get_session_name(item)
                if name == target_name:
                    logger.info("_open: 搜索+sibling 匹配 '%s'", name[:25])
                    item.Click(simulateMove=False)
                    time.sleep(0.5)
                    if _wait_chat_stable(target_name):
                        logger.info("_open: 搜索+sibling -> '%s'", target_name[:20])
                        return True
                    break
                item = item.GetNextSiblingControl()
                if item is None:
                    break
            except Exception:
                break

        logger.warning("_open_session 全部失败: '%s'", target_name[:20])
        return False

    def _wait_chat_stable(target_name, max_wait=3.0, check_interval=0.3):
        """验证窗口标题已切换到目标会话，连续两次匹配才确认。"""
        return wait_chat_stable(wx, target_name, max_wait, check_interval)

    def _return_to_session_list():
        """返回微信聊天列表，确保离开会话内部视图。"""
        try:
            wx.A_ChatIcon.DoubleClick(simulateMove=False)
        except Exception:
            _ensure_foreground(wx)
            wx.UiaAPI.SendKeys('{ESC}', waitTime=0.2)
        time.sleep(0.3)
        wx.UiaAPI.SendKeys('{ESC}', waitTime=0.2)
        time.sleep(0.2)

    # ── 构建待处理会话队列 ────────────────────────────────
    # 核心防护：只有未读数相比上次 Poll 增加时，才打开会话检查
    # 防止 WeChat 残留未读 badge 导致每 45s 劫持一次用户窗口
    now = time.time()
    pending_sessions = []
    for who, count in sessions.items():
        if not who or count <= 0:
            continue
        if who in SKIP_SESSIONS:
            continue
        if ALLOW_SESSIONS and who not in ALLOW_SESSIONS:
            continue
        if not rl.can_reply_session(who):
            continue
        # 空读冷却（已有无新消息的会话跳过）
        if who in _last_empty_read and now - _last_empty_read[who] < EMPTY_READ_COOLDOWN:
            continue
        # 新消息检测：只有 count > 上次快照才打开
        last_count = _last_session_counts.get(who, 0)
        if count <= last_count:
            logger.debug("跳过 [%s]: 未读数 %d ≤ 上次 %d (无新消息)", who[:20], count, last_count)
            continue
        pending_sessions.append(who)

    if not pending_sessions:
        return seen

    pending_sessions = pending_sessions[:MAX_SESSIONS_PER_CYCLE]

    # ═══════════════════════════════════════════════════════
    # Phase 1: 采集消息（批量窗口操作：打开→读取→ESC 返回）
    # ═══════════════════════════════════════════════════════
    pending_messages = []

    for who in pending_sessions:
        try:
            if not _open_session(who):
                logger.warning("Phase1: 无法打开会话 %s", who[:20])
                _return_to_session_list()
                continue

            # 二次验证：确保窗口稳定后再采集
            if not _wait_chat_stable(who, max_wait=2.0):
                logger.warning("Phase1: 打开后验证失败 %s，跳过", who[:20])
                _return_to_session_list()
                continue

            try:
                msgs = wx.GetAllMessage()
            except Exception as e:
                logger.warning("Phase1: GetAllMessage 失败 %s: %s", who[:20], e)
                _return_to_session_list()
                continue
        except Exception as e:
            logger.error("Phase1: 处理会话 %s 异常: %s", who[:20], e, exc_info=True)
            _return_to_session_list()
            continue

        if not msgs:
            _return_to_session_list()
            continue

        # 只取最新一条有效消息
        for msg in reversed(msgs):
            msg_type = getattr(msg, 'type', '')
            if msg_type not in ('friend',):
                continue
            content = getattr(msg, 'content', '') or ''
            sender = getattr(msg, 'sender', who)
            if not content or not isinstance(content, str) or not content.strip():
                continue
            if content.startswith(('[图片]', '[文件]', '[语音]',
                                    '[Image]', '[File]', '[Voice]')):
                continue
            if sender == 'Self':
                continue

            fp = msg_fingerprint(who, sender, content.strip())
            if fp in updated_seen:
                continue

            # @提及过滤
            mentions = re.findall(r'@\S+', content)
            if mentions:
                bot_nick = getattr(wx, 'nickname', 'HE')
                if any(m.lstrip('@') not in (bot_nick, '') for m in mentions):
                    logger.info("@过滤 [%s] %s: %s", who[:20], sender[:20], content[:60])
                    updated_seen.add(fp)
                    continue

            context_key = f"{who}|{sender}"
            session_id = f"wxauto_{sha256(f'{who}|{sender}'.encode()).hexdigest()[:12]}"
            history = ss.get_history(context_key)

            pending_messages.append({
                'who': who,
                'chat_name': who,
                'sender': sender,
                'content': content.strip(),
                'fp': fp,
                'context_key': context_key,
                'session_id': session_id,
                'history': history,
            })
            logger.info("Phase1 采集 [%s] %s: %s", who[:20], sender[:20], content[:60])
            break  # 每会话只取最新一条

        _return_to_session_list()

    # 空读冷却：已打开的会话中未找到新消息的，暂缓再查
    opened_who = {m['who'] for m in pending_messages}
    for who in pending_sessions:
        if who not in opened_who:
            _last_empty_read[who] = time.time()

    if not pending_messages:
        if len(updated_seen) > len(seen):
            save_seen(updated_seen)
        return updated_seen

    logger.info("Phase1 完成: 采集 %d 条消息 %d 个会话",
                len(pending_messages),
                len(set(m['who'] for m in pending_messages)))

    # ═══════════════════════════════════════════════════════
    # Phase 2: Hermes 推理（批量 HTTP 调用，微信窗口空闲）
    # ═══════════════════════════════════════════════════════
    reply_queue = []

    for pm in pending_messages:
        if not rl.can_reply(pm['who']):
            logger.warning("频率限制，跳过 %s", pm['who'][:20])
            continue

        delay = random.uniform(REPLY_DELAY_MIN, REPLY_DELAY_MAX)
        time.sleep(delay)

        reply, elapsed = call_hermes(
            pm['session_id'], pm['sender'], pm['content'], pm['history'])

        if reply:
            ss.add_user_message(pm['context_key'],
                               f"[{pm['sender']}]: {pm['content']}")
            ss.add_assistant_message(pm['context_key'], reply)
            pm['reply'] = reply
            reply_queue.append(pm)
            logger.info("Phase2 推理 [%s] %.1fs: %s",
                        pm['who'][:20], elapsed, reply[:60])
        else:
            logger.warning("Hermes 无回复 (%.1fs): %s", elapsed, pm['content'][:40])

    if not reply_queue:
        logger.info("Phase2: 无待投递回复")
        return updated_seen

    # ═══════════════════════════════════════════════════════
    # Phase 3: 投递回复（批量窗口操作：打开→发送→ESC 返回）
    # ═══════════════════════════════════════════════════════
    delivered = 0
    for pm in reply_queue:
        if not _open_session(pm['chat_name']):
            logger.warning("Phase3: 无法打开会话 %s，下轮重试", pm['who'][:20])
            _return_to_session_list()
            continue

        if not _wait_chat_stable(pm['chat_name'], max_wait=2.0):
            logger.warning("Phase3: 打开后验证失败 %s，跳过", pm['who'][:20])
            _return_to_session_list()
            continue

        if send_reply(wx, pm['chat_name'], pm['reply']):
            updated_seen.add(pm['fp'])
            rl.record_reply(pm['who'])
            delivered += 1
            # 回复成功后清除该会话的未读快照，避免下轮 count 相同被跳过
            _last_session_counts[pm['who']] = 0
            logger.info("Phase3 已回复 [%s]: %s", pm['who'][:20], pm['reply'][:60])
        else:
            logger.warning("Phase3: 发送失败 %s，下轮重试", pm['who'][:20])

        _return_to_session_list()

    # 保存本轮会话未读数快照，下轮 Poll 只有 count 增加才打开
    # 已回复的会话已清零，不再覆盖
    for who, count in sessions.items():
        if who not in _last_session_counts or _last_session_counts[who] != 0:
            _last_session_counts[who] = count

    save_seen(updated_seen)
    logger.info("本轮: Phase1=%d Phase2=%d Phase3=%d | 今日 %d/%d",
                len(pending_messages), len(reply_queue), delivered,
                rl.daily_total, DAILY_TOTAL_LIMIT)

    return updated_seen

# ═══════════════════════════════════════════════════════════════
# ── 主循环 ───────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

def main():
    logger.info("=" * 60)
    logger.info("wxauto → Hermes qljk 桥接启动")
    logger.info("Hermes API: %s", HERMES_API_URL)
    logger.info("活跃时段: %02d:00 - %02d:00 (UTC+8)", ACTIVE_START_HOUR, ACTIVE_END_HOUR)
    logger.info("上下文隔离: {聊天名}|{说话人} (群聊中每人独立上下文)")
    logger.info("日上限: %d 条 | 单用户小时上限: %d 条", DAILY_TOTAL_LIMIT, PER_USER_HOURLY_LIMIT)
    logger.info("回复延迟: %.0f-%.0f 秒 | 轮询间隔: %d±%.0f 秒",
                REPLY_DELAY_MIN, REPLY_DELAY_MAX, POLL_INTERVAL, POLL_JITTER)
    logger.info("=" * 60)

    # ── PID 文件锁：防止多实例运行 ────────────────────
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            # 检查该 PID 是否仍在运行
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0400, False, old_pid)  # PROCESS_QUERY_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                logger.error("检测到已有桥接进程运行 (PID: %d)，退出", old_pid)
                logger.error("如需重启请先结束该进程，或删除 %s", PID_FILE)
                sys.exit(1)
        except Exception:
            pass  # PID 无效，覆盖
    PID_FILE.write_text(str(os.getpid()))

    # 检查 Hermes 是否可达
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8647/v1/models",
            headers={"Authorization": "Bearer local"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m["id"] for m in data.get("data", [])]
            logger.info("Hermes API 连通，可用模型: %s", models)
    except Exception as e:
        logger.error("Hermes API 不可达 (127.0.0.1:8647): %s", e)
        logger.error("请确认 Hermes qljk gateway 是否运行")
        sys.exit(1)

    # 加载状态
    seen = load_seen()
    rl = RateLimiter()
    ss = SessionStore()
    logger.info("已加载 %d 条消息指纹 | %d 个会话历史 | 今日已回复: %d/%d",
                len(seen), len(ss.sessions), rl.daily_total, DAILY_TOTAL_LIMIT)

    # 初始化 wxauto
    logger.info("正在连接微信客户端...")
    try:
        from wxauto import WeChat
        wx = WeChat(language="cn")
        logger.info("微信连接成功: %s", wx.nickname if hasattr(wx, "nickname") else "ok")
    except Exception as e:
        logger.error("连接微信失败: %s", e)
        sys.exit(1)

    # ── 窗口控制 ────────────────────────────────────────
    if MINIMIZE_ON_START:
        minimize_wechat(wx)

    # ── 启动清积压：启动前的未读消息全部标记已读，不回复 ──
    seen = prime_startup_messages(wx, seen)

    # ── UIA 稳定化：双击"聊天"图标确保在聊天列表，避免 GetSessionList 崩溃 ──
    # 注意：不调用 SwitchToThisWindow()，避免无消息时抢夺用户焦点
    logger.info("UIA 稳定化...")
    try:
        wx.A_ChatIcon.DoubleClick(simulateMove=False)
        time.sleep(0.5)
        logger.info("UIA 稳定化完成")
    except Exception as e:
        logger.warning("UIA 稳定化异常（非致命）: %s", e)

    # 平滑退出
    running = True
    def handle_signal(sig, frame):
        nonlocal running
        logger.info("收到退出信号，保存状态... stats=%s", rl.get_stats())
        save_seen(seen)
        ss._save()
        try: PID_FILE.unlink(missing_ok=True)
        except Exception: pass
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # 主循环
    error_count = 0
    last_stats_log = time.time()

    while running:
        try:
            # ── 活跃时段检查 ────────────────────────────
            if rl.is_active_hours():
                new_seen = process_unread(wx, seen, rl, ss)
                if new_seen is not None:
                    seen = new_seen
                error_count = 0
            else:
                # 非活跃时段，降低轮询频率
                time.sleep(30)
                continue

        except Exception as e:
            error_count += 1
            logger.error("主循环异常 (%d): %s", error_count, e)
            if error_count > 10:
                logger.critical("连续错误过多，退出")
                break
            time.sleep(POLL_INTERVAL * 2)

        # ── 定期输出统计 ────────────────────────────────
        if time.time() - last_stats_log > 600:  # 每 10 分钟
            stats = rl.get_stats()
            logger.info("统计: 今日回复 %d/%d, 活跃用户 %d",
                        stats["daily_total"], stats["daily_limit"],
                        stats["active_users"])
            last_stats_log = time.time()

        # ── 随机抖动轮询间隔 ────────────────────────────
        jitter = random.uniform(-POLL_JITTER, POLL_JITTER)
        time.sleep(max(1.0, POLL_INTERVAL + jitter))

    logger.info("桥接已停止 | 最终: %s", rl.get_stats())

if __name__ == "__main__":
    main()
