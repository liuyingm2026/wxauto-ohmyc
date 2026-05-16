#!/usr/bin/env python3
"""WeChat 3.9.11 ChatBox UIA 深度诊断

绕过 wxauto 包的 relative import 问题，直接导入底层模块。"""
import sys
import time

# 将 wxauto 包目录加入 path，方便直接 import uiautomation
PKG_DIR = r"C:\Users\ohmyc\AppData\Roaming\Python\Python312\site-packages\wxauto"
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

import uiautomation as uia
import win32gui
import win32con
import ctypes
import pyperclip

uia.SetGlobalSearchTimeout(10)


def traverse_tree(control, depth=0, max_depth=5):
    if depth > max_depth:
        return
    try:
        children = control.GetChildren()
        indent = "  " * depth
        for child in children:
            try:
                ctype = child.ControlTypeName
                name = (child.Name or '')[:60]
                classname = (child.ClassName or '')[:30]
                rect = child.BoundingRectangle
                w = rect.width() if rect else 0
                h = rect.height() if rect else 0
                if w == 0 and not name:
                    continue
                print(f"{indent}{ctype} Name='{name}' Class='{classname}' ({rect.left},{rect.top},{rect.right},{rect.bottom})[{w}x{h}]")
                traverse_tree(child, depth + 1, max_depth)
            except Exception:
                pass
    except Exception as e:
        print(f"{'  ' * depth}[Error: {e}]")


def main():
    print("=" * 60)
    print("WeChat 3.9.11 ChatBox UIA 深度诊断")
    print("=" * 60)

    hwnd = win32gui.FindWindow('WeChatMainWndForPC', None)
    if not hwnd:
        print("未找到微信窗口！")
        return
    print(f"微信窗口 HWND={hwnd}")
    title = win32gui.GetWindowText(hwnd)
    print(f"Win32 标题: '{title}'")

    wx_window = uia.WindowControl(ClassName='WeChatMainWndForPC', searchDepth=1)
    print(f"UIA 窗口 Name='{wx_window.Name}'")

    # Get main layout
    children = wx_window.GetChildren()
    print(f"\n窗口直接子元素: {len(children)}")
    for i, c in enumerate(children):
        print(f"  [{i}] {c.ControlTypeName} Class='{c.ClassName}' Name='{(c.Name or '')[:40]}'")

    main1 = [i for i in children if not i.ClassName]
    if not main1:
        print("未找到 MainControl1")
        return

    main2 = main1[0].GetFirstChildControl()
    if not main2:
        print("未找到 MainControl2")
        return

    inner = main2.GetChildren()
    print(f"\nMainControl2 子元素 (A|B|C 布局):")
    for i, c in enumerate(inner):
        rc = c.BoundingRectangle
        print(f"  [{i}] {c.ControlTypeName} Class='{c.ClassName}' ({rc.left},{rc.top},{rc.right},{rc.bottom})[{rc.width()}x{rc.height()}]")

    if len(inner) < 3:
        print("布局不完整")
        return

    sessionbox = inner[1]
    chatbox = inner[2]
    print(f"\nSessionBox: rect=({sessionbox.BoundingRectangle.left},{sessionbox.BoundingRectangle.top},{sessionbox.BoundingRectangle.right},{sessionbox.BoundingRectangle.bottom})")
    print(f"ChatBox: rect=({chatbox.BoundingRectangle.left},{chatbox.BoundingRectangle.top},{chatbox.BoundingRectangle.right},{chatbox.BoundingRectangle.bottom})")

    # 1. C_MsgList check
    print("\n[1] C_MsgList (ListControl Name='消息'):")
    try:
        uia.SetGlobalSearchTimeout(1500)
        msglist = chatbox.ListControl(Name='消息')
        exists = msglist.Exists(0.5)
        print(f"  Exists: {exists}")
        if exists:
            items = msglist.GetChildren()
            print(f"  Children: {len(items)}")
            for item in items[:5]:
                rc = item.BoundingRectangle
                print(f"    {item.ControlTypeName} Name='{(item.Name or '')[:60]}' ({rc.left},{rc.top},{rc.right},{rc.bottom})")
    except Exception as e:
        print(f"  异常: {e}")
    finally:
        uia.SetGlobalSearchTimeout(10000)

    # 2. Full ChatBox tree
    print("\n[2] ChatBox UIA 树:")
    traverse_tree(chatbox, max_depth=5)

    # 3. Try to find any ListControl at any depth
    print("\n[3] ChatBox 内 ListControl 深度搜索:")
    try:
        uia.SetGlobalSearchTimeout(2000)
        for sd in [2, 3, 5, 8, 12, 20]:
            try:
                lc = chatbox.ListControl(searchDepth=sd)
                if lc.Exists(0.1):
                    name = lc.Name or ''
                    items = lc.GetChildren()
                    print(f"  searchDepth={sd}: Name='{name[:80]}' Children={len(items)}")
                    for i, item in enumerate(items[:3]):
                        try:
                            rc = item.BoundingRectangle
                            print(f"    [{i}] {item.ControlTypeName} Name='{(item.Name or '')[:80]}' ({rc.left},{rc.top},{rc.right},{rc.bottom})")
                        except Exception:
                            print(f"    [{i}] (不可读)")
            except Exception:
                pass
    except Exception as e:
        print(f"  异常: {e}")
    finally:
        uia.SetGlobalSearchTimeout(10000)

    # 4. Try to find TextControl for chat name
    print("\n[4] 当前聊天名称检测:")
    try:
        uia.SetGlobalSearchTimeout(1000)
        for sd in [3, 5, 10, 15, 20]:
            try:
                tc = chatbox.TextControl(searchDepth=sd)
                if tc.Exists(0.1):
                    print(f"  TextControl searchDepth={sd}: Name='{tc.Name}'")
                    break
            except Exception:
                pass
        else:
            print("  TextControl: 所有深度都找不到")
    except Exception as e:
        print(f"  异常: {e}")
    finally:
        uia.SetGlobalSearchTimeout(10000)
    print(f"  Win32 窗口标题: '{win32gui.GetWindowText(hwnd)}'")

    # 5. SessionBox items
    print("\n[5] SessionBox 可见会话项:")
    try:
        uia.SetGlobalSearchTimeout(2000)
        items = sessionbox.GetChildren()
        for i, c in enumerate(items[:15]):
            try:
                rc = c.BoundingRectangle
                name = (c.Name or '')[:80]
                if rc.width() > 0:
                    has_unread = '新消息' in name
                    mark = ' [!]' if has_unread else ''
                    print(f"  [{i}] {c.ControlTypeName} '{name}' ({rc.left},{rc.top},{rc.right},{rc.bottom}){mark}")
            except Exception:
                pass
    except Exception as e:
        print(f"  异常: {e}")
    finally:
        uia.SetGlobalSearchTimeout(10000)

    # 6. Clipboard test with multiple approaches
    print("\n[6] 剪贴板读取测试:")
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.3)

        foreground = win32gui.GetForegroundWindow()
        cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        fg_thread = ctypes.windll.user32.GetWindowThreadProcessId(foreground, 0)
        if cur_thread != fg_thread:
            ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, True)
            win32gui.SetForegroundWindow(hwnd)
            ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, False)
        else:
            win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.5)
        print("  窗口已前置")

        import pyautogui
        pyautogui.FAILSAFE = False

        left, top, right, bottom = chatbox.BoundingRectangle
        # Message area: left portion of ChatBox (excluding toolbar area on right)
        msg_left = left + int((right - left) * 0.15)
        msg_right = left + int((right - left) * 0.55)
        msg_top = top + 60
        msg_bottom = bottom - 100
        print(f"  消息区域: ({msg_left},{msg_top},{msg_right},{msg_bottom})")

        for attempt in range(4):
            pyperclip.copy("")
            time.sleep(0.05)

            cx = (msg_left + msg_right) // 2
            cy = msg_bottom - (attempt * 120)  # Click at different heights from bottom up
            print(f"  [尝试{attempt+1}] 点击 ({cx},{cy})")

            pyautogui.click(cx, cy)
            time.sleep(0.3)

            # Try triple-click to select message
            pyautogui.click(cx, cy)
            time.sleep(0.08)
            pyautogui.click(cx, cy)
            time.sleep(0.08)
            pyautogui.click(cx, cy)
            time.sleep(0.15)
            pyautogui.hotkey('ctrl', 'c')
            time.sleep(0.3)

            content = pyperclip.paste()
            if content and content.strip():
                print(f"  [尝试{attempt+1}] 成功! ({len(content)} 字符):")
                for line in content.strip().split('\n')[:10]:
                    print(f"    {line[:200]}")
                return
            print(f"  [尝试{attempt+1}] 空")
    except Exception as e:
        print(f"  剪贴板异常: {e}")

    print("\n" + "=" * 60)
    print("诊断完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
