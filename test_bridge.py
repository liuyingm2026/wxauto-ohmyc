#!/usr/bin/env python3
"""wxauto Bridge 健康检查脚本

验证 bridge 各组件的运行状态，不发送消息（bot 无法为自己创建未读 badge）。
用法:
    python test_bridge.py                # 完整健康检查
    python test_bridge.py --quick        # 快速检查（跳过 Hermes）
    python test_bridge.py --json         # JSON 输出
"""

import json
import os
import sys
import time
import argparse
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

BRIDGE_DIR = Path(__file__).parent
LOG_PATH = BRIDGE_DIR / "data" / "bridge.log"
PID_PATH = BRIDGE_DIR / "data" / "bridge.pid"

# 检查项权重
WEIGHTS = {
    "process": 20,
    "wechat": 20,
    "session_list": 15,
    "hermes": 20,
    "recent_polling": 15,
    "recent_reply": 10,
}


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")


def check_bridge_running():
    """检查 bridge 进程是否存活"""
    if not PID_PATH.exists():
        return False, "PID 文件不存在"
    try:
        pid = int(PID_PATH.read_text().strip())
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x100000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True, f"PID={pid}"
        return False, f"PID={pid} 进程不存在"
    except Exception as e:
        return False, str(e)


def check_wechat():
    """检查微信连接"""
    try:
        from wxauto import WeChat
        wx = WeChat()
        nickname = wx.nickname
        return True, f"已登录: {nickname}"
    except Exception as e:
        return False, str(e)


def check_session_list():
    """检查 GetSessionList 是否正常工作"""
    try:
        from wxauto import WeChat
        wx = WeChat()
        # 返回聊天列表
        wx.A_ChatIcon.DoubleClick(simulateMove=False)
        time.sleep(0.5)
        sessions = wx.GetSessionList()
        if sessions:
            return True, f"检测到 {len(sessions)} 个会话"
        return False, "GetSessionList 返回空"
    except Exception as e:
        return False, str(e)


def check_hermes():
    """检查 Hermes API 是否可达"""
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:8647/v1/models",
            headers={"Authorization": "Bearer n/a"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        models = [m.get("id", "?") for m in data.get("data", [])]
        return True, f"模型: {', '.join(models)}" if models else "已连接（无模型列表）"
    except Exception as e:
        return False, str(e)


def check_recent_activity(log_path, minutes=5):
    """检查最近 N 分钟内是否有 bridge 活动"""
    if not log_path.exists():
        return False, "日志文件不存在", {}

    tz = timezone(timedelta(hours=8))  # UTC+8
    cutoff = datetime.now(tz) - timedelta(minutes=minutes)

    details = {"polling": False, "replies": [], "last_line": ""}

    with open(log_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    if not lines:
        return False, "日志为空", details

    details["last_line"] = lines[-1].strip()

    recent_count = 0
    for line in lines:
        # 解析时间戳: 2026-05-15 23:03:31,210
        m = re.match(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        if m:
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
                if ts >= cutoff:
                    recent_count += 1
                    if "Phase3 已回复" in line:
                        details["replies"].append(line.strip())
            except ValueError:
                pass

    if recent_count > 0:
        if details["replies"]:
            return True, f"近 {minutes} 分钟有 {recent_count} 条日志，{len(details['replies'])} 条回复", details
        if "会话变化" in lines[-1] or any("会话变化" in l for l in lines[-20:]):
            return True, f"近 {minutes} 分钟有 {recent_count} 条日志（活跃轮询中）", details
        return True, f"近 {minutes} 分钟有 {recent_count} 条日志（无新消息）", details

    return False, f"近 {minutes} 分钟无日志活动，bridge 可能已停摆", details


def main():
    parser = argparse.ArgumentParser(description="wxauto Bridge 健康检查")
    parser.add_argument("--quick", action="store_true", help="跳过 Hermes 检查")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--minutes", type=int, default=5, help="最近活动检查窗口（分钟，默认 5）")
    args = parser.parse_args()

    results = {}
    all_checks = []

    # 1. 进程检查
    ok, msg = check_bridge_running()
    results["process"] = {"ok": ok, "msg": msg}
    all_checks.append(("Bridge 进程", ok, msg))

    if not ok:
        # Bridge 没跑，后续检查无意义
        results["wechat"] = {"ok": False, "msg": "跳过（bridge 未运行）"}
        results["session_list"] = {"ok": False, "msg": "跳过（bridge 未运行）"}
        results["hermes"] = {"ok": False, "msg": "跳过（bridge 未运行）"}
        results["recent"] = {"ok": False, "msg": "跳过（bridge 未运行）"}
        _print_results(results, all_checks, args)
        return 1

    # 2. 微信连接
    ok, msg = check_wechat()
    results["wechat"] = {"ok": ok, "msg": msg}
    all_checks.append(("微信连接", ok, msg))

    # 3. GetSessionList
    if ok:
        ok, msg = check_session_list()
        results["session_list"] = {"ok": ok, "msg": msg}
        all_checks.append(("会话列表", ok, msg))
    else:
        results["session_list"] = {"ok": False, "msg": "跳过（微信未连接）"}
        all_checks.append(("会话列表", False, "跳过（微信未连接）"))

    # 4. Hermes API
    if not args.quick:
        ok, msg = check_hermes()
        results["hermes"] = {"ok": ok, "msg": msg}
        all_checks.append(("Hermes API", ok, msg))
    else:
        results["hermes"] = {"ok": True, "msg": "跳过（--quick）"}
        all_checks.append(("Hermes API", True, "跳过（--quick）"))

    # 5. 近期活动
    ok, msg, details = check_recent_activity(LOG_PATH, args.minutes)
    results["recent"] = {"ok": ok, "msg": msg, "details": details}
    all_checks.append((f"近期活动 ({args.minutes}min)", ok, msg))

    _print_results(results, all_checks, args)

    all_ok = all(c["ok"] for c in results.values())
    return 0 if all_ok else 1


def _print_results(results, all_checks, args):
    if args.json:
        output = {
            "timestamp": datetime.now().isoformat(),
            "checks": results,
            "overall": all(c["ok"] for c in results.values()),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    print("=" * 50)
    print("wxauto Bridge 健康检查")
    print(f"  日志: {LOG_PATH}")
    print(f"  PID:  {PID_PATH}")
    print()

    total_weight = 0
    earned_weight = 0

    for label, ok, msg in all_checks:
        status = "[OK]" if ok else "[FAIL]"
        w = WEIGHTS.get(
            "process" if "进程" in label else
            "wechat" if "微信" in label else
            "session_list" if "会话" in label else
            "hermes" if "Hermes" in label else
            "recent_reply" if "回复" in msg else
            "recent_polling", 10
        )
        # Map the check to weight
        if "进程" in label:
            w = WEIGHTS["process"]
        elif "微信" in label:
            w = WEIGHTS["wechat"]
        elif "会话" in label:
            w = WEIGHTS["session_list"]
        elif "Hermes" in label:
            w = WEIGHTS["hermes"]
        elif "近期" in label:
            w = WEIGHTS["recent_polling"]
            if "回复" in msg:
                w = WEIGHTS["recent_reply"] + WEIGHTS["recent_polling"]
        else:
            w = 10

        total_weight += w if ok else 0
        earned_weight += w
        print(f"  {status} {label}: {msg}")

    print()
    score = round(total_weight / earned_weight * 100) if earned_weight > 0 else 0
    if score >= 90:
        print(f"健康评分: {score}/100 — Bridge 运行正常")
    elif score >= 60:
        print(f"健康评分: {score}/100 — 部分组件异常，请检查")
    else:
        print(f"健康评分: {score}/100 — Bridge 存在严重问题")
        print(f"  建议: 检查微信是否登录，重启 bridge")


if __name__ == "__main__":
    sys.exit(main())
