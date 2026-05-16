#!/usr/bin/env python3
"""Part 3: Open chat with proper foreground, then explore UIA tree"""
import sys
import time

PKG_DIR = r"C:\Users\ohmyc\AppData\Roaming\Python\Python312\site-packages\wxauto"
sys.path.insert(0, PKG_DIR)

import uiautomation as uia
import win32gui
import win32con
import ctypes
import pyperclip

uia.SetGlobalSearchTimeout(10)


def force_foreground(hwnd):
    """Force a window to foreground"""
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.2)
        foreground = win32gui.GetForegroundWindow()
        if foreground == hwnd:
            return True
        cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        fg_thread = ctypes.windll.user32.GetWindowThreadProcessId(foreground, 0)
        ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, True)
        result = win32gui.SetForegroundWindow(hwnd)
        ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, False)
        time.sleep(0.3)
        return result != 0
    except Exception as e:
        print(f"  force_foreground 异常: {e}")
        return False


def dump_with_msg_check(control, max_depth=6):
    """Dump tree and check for message-like content"""
    results = []
    def _dump(c, depth):
        if depth > max_depth:
            return
        try:
            for child in c.GetChildren():
                try:
                    ctype = child.ControlTypeName
                    name = (child.Name or '')
                    cls = (child.ClassName or '')
                    rect = child.BoundingRectangle
                    w, h = rect.width(), rect.height()
                    if w > 0 and (name or ctype in ('TextControl', 'EditControl', 'ListControl', 'ListItemControl')):
                        indent = "  " * depth
                        print(f"{indent}{ctype} Name='{name[:100]}' Class='{cls}' ({rect.left},{rect.top},{rect.right},{rect.bottom})[{w}x{h}]")
                        if name:
                            results.append((depth, ctype, name, rect))
                    _dump(child, depth + 1)
                except Exception:
                    pass
        except Exception:
            pass
    _dump(control, 0)
    return results


def main():
    print("=" * 60)
    print("Part 3: 带前景焦点的UIA诊断")
    print("=" * 60)

    hwnd = win32gui.FindWindow('WeChatMainWndForPC', None)
    if not hwnd:
        print("未找到微信窗口！")
        return

    print(f"微信 HWND={hwnd}")
    print(f"当前窗口标题: '{win32gui.GetWindowText(hwnd)}'")

    # Step 1: Force foreground
    print("\n[Step 1] 强制前置微信窗口...")
    ok = force_foreground(hwnd)
    print(f"  结果: {'成功' if ok else '失败'}")
    time.sleep(0.5)
    print(f"  窗口标题: '{win32gui.GetWindowText(hwnd)}'")

    # Step 2: Build UIA tree
    wx_window = uia.WindowControl(ClassName='WeChatMainWndForPC', searchDepth=1)
    children = wx_window.GetChildren()
    main1 = [i for i in children if not i.ClassName][0]
    main2 = main1.GetFirstChildControl()
    inner = main2.GetChildren()
    sessionbox = inner[1]
    chatbox = inner[2]

    # Step 3: Find and click a session with unread
    print("\n[Step 2] 查找未读会话并双击打开...")
    try:
        uia.SetGlobalSearchTimeout(3000)
        lc = sessionbox.ListControl(Name='会话', searchDepth=5)
        if lc.Exists(0.3):
            items = lc.GetChildren()
            print(f"  找到 {len(items)} 个会话")
            for item in items[:15]:
                name = item.Name or ''
                if '新消息' in name:
                    print(f"  点击: '{name[:60]}'")
                    # Try double-click for more reliable opening
                    item.DoubleClick(simulateMove=False)
                    time.sleep(1.0)
                    new_title = win32gui.GetWindowText(hwnd)
                    print(f"  新窗口标题: '{new_title}'")
                    if new_title != '微信':
                        print("  *** 聊天已打开！***")
                    else:
                        print("  *** 标题未变，尝试再次点击... ***")
                        item.Click(simulateMove=False)
                        time.sleep(1.0)
                        new_title = win32gui.GetWindowText(hwnd)
                        print(f"  再次点击后标题: '{new_title}'")
                    break
    finally:
        uia.SetGlobalSearchTimeout(10000)

    # Step 4: Explore ChatBox
    print(f"\n[Step 3] ChatBox UIA树 (标题='{win32gui.GetWindowText(hwnd)}'):")
    all_named = dump_with_msg_check(chatbox, max_depth=7)

    # Step 5: Try C_MsgList again
    print("\n[Step 4] C_MsgList 和 ListControl 检查:")
    try:
        uia.SetGlobalSearchTimeout(2000)
        msglist = chatbox.ListControl(Name='消息')
        print(f"  ListControl Name='消息': Exists={msglist.Exists(0.5)}")
        if msglist.Exists(0.2):
            items = msglist.GetChildren()
            print(f"  Children: {len(items)}")
            for i, it in enumerate(items[:5]):
                print(f"    [{i}] {it.ControlTypeName} Name='{(it.Name or '')[:80]}'")

        # Also check for ANY ListControl
        for sd in [3, 5, 8, 12]:
            try:
                any_lc = chatbox.ListControl(searchDepth=sd)
                if any_lc.Exists(0.1):
                    name = any_lc.Name or ''
                    cnt = len(any_lc.GetChildren())
                    print(f"  ListControl searchDepth={sd}: Name='{name[:60]}' Children={cnt}")
                    if cnt > 0:
                        for i, it in enumerate(any_lc.GetChildren()[:5]):
                            print(f"    [{i}] {it.ControlTypeName} Name='{(it.Name or '')[:80]}'")
            except Exception:
                pass
    except Exception as e:
        print(f"  异常: {e}")
    finally:
        uia.SetGlobalSearchTimeout(10000)

    # Step 6: Also check if the deepest PaneControl in ChatBox has items
    print("\n[Step 5] ChatBox 最深层 PaneControl 探索:")
    try:
        uia.SetGlobalSearchTimeout(2000)
        # Get the deepest PaneControl structure
        # ChatBox structure from earlier: PaneControl(248x640), PaneControl(110x640){3 sub}, PaneControl(248x640)
        for i, child in enumerate(chatbox.GetChildren()):
            if child.ControlTypeName == 'PaneControl':
                rc = child.BoundingRectangle
                sub_children = child.GetChildren()
                if sub_children:
                    print(f"  PaneControl[{i}] ({rc.left},{rc.top},{rc.right},{rc.bottom}): {len(sub_children)} 子元素")
                    for j, sub in enumerate(sub_children[:10]):
                        sub_rc = sub.BoundingRectangle
                        print(f"    [{j}] {sub.ControlTypeName} Name='{(sub.Name or '')[:60]}' ({sub_rc.left},{sub_rc.top},{sub_rc.right},{sub_rc.bottom})")
                        # Go one more level
                        try:
                            subsub = sub.GetChildren()
                            for k, ss in enumerate(subsub[:5]):
                                try:
                                    ss_rc = ss.BoundingRectangle
                                    print(f"      [{k}] {ss.ControlTypeName} Name='{(ss.Name or '')[:80]}' ({ss_rc.left},{ss_rc.top},{ss_rc.right},{ss_rc.bottom})")
                                    # One more
                                    try:
                                        sss = ss.GetChildren()
                                        for l, s in enumerate(sss[:3]):
                                            try:
                                                s_rc = s.BoundingRectangle
                                                print(f"        [{l}] {s.ControlTypeName} Name='{(s.Name or '')[:80]}' ({s_rc.left},{s_rc.top},{s_rc.right},{s_rc.bottom})")
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                        except Exception:
                            pass
    except Exception as e:
        print(f"  异常: {e}")
    finally:
        uia.SetGlobalSearchTimeout(10000)

    # Step 7: Try clipboard with full focus
    print("\n[Step 6] 剪贴板测试 (聊天窗口内):")
    if force_foreground(hwnd):
        print("  窗口已前置")
        time.sleep(0.5)

        import pyautogui
        pyautogui.FAILSAFE = False

        left, top, right, bottom = chatbox.BoundingRectangle
        msg_left = left + 30
        msg_right = left + int((right - left) * 0.55)
        msg_bottom = bottom - 60

        for attempt in range(5):
            pyperclip.copy("")
            time.sleep(0.05)
            cx = (msg_left + msg_right) // 2
            cy = msg_bottom - (attempt * 100)  # Try different vertical positions
            print(f"  [尝试{attempt+1}] 点击({cx},{cy}), Ctrl+A, Ctrl+C")

            pyautogui.click(cx, cy)
            time.sleep(0.3)
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.3)
            pyautogui.hotkey('ctrl', 'c')
            time.sleep(0.4)

            content = pyperclip.paste()
            if content and content.strip():
                print(f"  *** 成功! ({len(content)} 字符) ***")
                for line in content.strip().split('\n')[:15]:
                    print(f"    {line[:200]}")
                return
            print(f"    空")

    print("\n" + "=" * 60)
    print("诊断完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
