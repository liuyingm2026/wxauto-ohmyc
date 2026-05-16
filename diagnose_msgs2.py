#!/usr/bin/env python3
"""WeChat 3.9.11 深度诊断 Part 2: 深入 SessionBox + 打开聊天后检查 ChatBox"""
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


def dump_tree(control, max_depth=6):
    """Print full tree including all children recursively"""
    def _dump(c, depth):
        if depth > max_depth:
            return
        try:
            indent = "  " * depth
            children = c.GetChildren()
            for child in children:
                try:
                    ctype = child.ControlTypeName
                    name = (child.Name or '')[:80]
                    cls = (child.ClassName or '')[:30]
                    rect = child.BoundingRectangle
                    w, h = rect.width(), rect.height()
                    if w > 0 or name:
                        runtime_id = child.GetRuntimeId() if hasattr(child, 'GetRuntimeId') else ''
                        print(f"{indent}{ctype} Name='{name}' Class='{cls}' ({rect.left},{rect.top},{rect.right},{rect.bottom})[{w}x{h}]")
                        _dump(child, depth + 1)
                except Exception:
                    pass
        except Exception as e:
            print(f"{'  ' * depth}[Error: {e}]")

    _dump(control, 0)


def main():
    print("=" * 60)
    print("WeChat 3.9.11 深度诊断 Part 2")
    print("=" * 60)

    hwnd = win32gui.FindWindow('WeChatMainWndForPC', None)
    if not hwnd:
        print("未找到微信窗口！")
        return
    print(f"微信 HWND={hwnd}, 标题='{win32gui.GetWindowText(hwnd)}'")

    wx_window = uia.WindowControl(ClassName='WeChatMainWndForPC', searchDepth=1)
    children = wx_window.GetChildren()
    main1 = [i for i in children if not i.ClassName][0]
    main2 = main1.GetFirstChildControl()
    inner = main2.GetChildren()
    navbox = inner[0]
    sessionbox = inner[1]
    chatbox = inner[2]

    # Part A: Deep dive into SessionBox
    print("\n===== SessionBox 深度探索 =====")
    print(f"SessionBox rect: ({sessionbox.BoundingRectangle.left},{sessionbox.BoundingRectangle.top},{sessionbox.BoundingRectangle.right},{sessionbox.BoundingRectangle.bottom})[{sessionbox.BoundingRectangle.width()}x{sessionbox.BoundingRectangle.height()}]")

    print("\nSessionBox 完整UIA树:")
    dump_tree(sessionbox, max_depth=7)

    # Part B: List all named controls in sessionbox
    print("\nSessionBox 内所有命名控件:")
    def find_named(control, max_depth=8):
        results = []
        def _s(c, d):
            if d > max_depth:
                return
            try:
                for child in c.GetChildren():
                    try:
                        name = child.Name
                        if name:
                            rect = child.BoundingRectangle
                            results.append((d, child.ControlTypeName, name[:100],
                                          f"({rect.left},{rect.top},{rect.right},{rect.bottom})"))
                        _s(child, d + 1)
                    except Exception:
                        pass
            except Exception:
                pass
        _s(control, 0)
        return results

    named = find_named(sessionbox, max_depth=8)
    for d, ctype, name, rect in named[:40]:
        print(f"  [d={d}] {ctype} '{name}' @ {rect}")

    # Part C: Get session list items (using wxauto's approach)
    print("\nSessionBox 内 ListItemControl 搜索:")
    try:
        uia.SetGlobalSearchTimeout(2000)
        for sd in [2, 3, 5, 8, 12]:
            try:
                items = sessionbox.ListItemControl(searchDepth=sd)
                if items.Exists(0.1):
                    name = items.Name or ''
                    print(f"  searchDepth={sd}: 找到首个ListItemControl: Name='{name[:80]}'")
                    # Try getting all
                    break
            except Exception:
                pass
    finally:
        uia.SetGlobalSearchTimeout(10000)

    # Part D: Now try to open a specific session and explore ChatBox
    print("\n===== 打开聊天会话并检查 ChatBox =====")

    # Find all ListItemControls with unread
    print("\n查找有未读的会话...")
    try:
        uia.SetGlobalSearchTimeout(3000)
        # Try getting all session items via different methods
        # Method 1: ListItemControl at searchDepth=8
        all_children = list(sessionbox.GetChildren())
        print(f"  SessionBox 直接子元素: {len(all_children)}")

        # Explore the session list - it might be hidden inside a PaneControl
        # The SessionBox (at 1142,88,1392,728) width=250 shows it's narrow sidebar
        # Items might be inside a ListControl
        for child in all_children:
            ctype = child.ControlTypeName
            cls = child.ClassName or ''
            rect = child.BoundingRectangle
            print(f"  {ctype} Class='{cls}' ({rect.left},{rect.top},{rect.right},{rect.bottom})[{rect.width()}x{rect.height()}]")
            if ctype in ('ListControl', 'PaneControl'):
                sub = child.GetChildren()
                print(f"    子元素数: {len(sub)}")
                for s in sub[:5]:
                    try:
                        print(f"      {s.ControlTypeName} Name='{(s.Name or '')[:80]}'")
                    except Exception:
                        pass

        # Try to find ListControl in SessionBox
        try:
            lc = sessionbox.ListControl(searchDepth=5)
            if lc.Exists(0.2):
                lc_name = lc.Name or ''
                lc_items = lc.GetChildren()
                print(f"\n  找到 ListControl: Name='{lc_name[:60]}', Children={len(lc_items)}")
                for i, item in enumerate(lc_items[:15]):
                    try:
                        print(f"    [{i}] {item.ControlTypeName} Name='{(item.Name or '')[:80]}'")
                    except Exception:
                        print(f"    [{i}] (不可读)")
        except Exception as e:
            print(f"  ListControl搜索异常: {e}")

        # Method 2: Get list items via siblings
        print("\n  GetChildren + 过滤 visible:")
        visible_items = []
        for child in all_children:
            try:
                if child.BoundingRectangle.width() > 0:
                    name = child.Name or ''
                    if name:
                        visible_items.append((child.ControlTypeName, name))
            except Exception:
                pass
        print(f"  可见命名项: {len(visible_items)}")
        for ctype, name in visible_items[:15]:
            print(f"    {ctype}: '{name}'")

    finally:
        uia.SetGlobalSearchTimeout(10000)

    # Part E: Click on first session with unread and check ChatBox
    print("\n===== 尝试打开第一个未读会话 =====")
    try:
        uia.SetGlobalSearchTimeout(3000)
        # Find sessions with "条新消息" in name
        lc = sessionbox.ListControl(searchDepth=8)
        if lc.Exists(0.3):
            items = lc.GetChildren()
            print(f"  ListControl items: {len(items)}")
            unread_found = False
            for item in items[:20]:
                try:
                    name = item.Name or ''
                    ctype = item.ControlTypeName
                    has_unread = '新消息' in name
                    print(f"    {ctype} Name='{name[:80]}' {'[!]' if has_unread else ''}")
                    if has_unread and not unread_found:
                        # Click this item
                        print(f"\n  点击会话: '{name[:60]}'")
                        item.Click(simulateMove=False)
                        time.sleep(1.5)
                        unread_found = True
                except Exception as e:
                    print(f"    (异常: {e})")

            if unread_found:
                # After clicking, explore ChatBox
                time.sleep(1.0)
                print(f"\n  窗口标题: '{win32gui.GetWindowText(hwnd)}'")

                print("\n  ChatBox 树 (打开会话后):")
                dump_tree(chatbox, max_depth=6)

                # Look for ListControl again
                print("\n  C_MsgList 重新检查:")
                try:
                    uia.SetGlobalSearchTimeout(1500)
                    msglist = chatbox.ListControl(Name='消息')
                    print(f"    Exists: {msglist.Exists(0.5)}")
                    if msglist.Exists(0.2):
                        items = msglist.GetChildren()
                        print(f"    Children: {len(items)}")
                        for item in items[:5]:
                            print(f"      {item.ControlTypeName} Name='{(item.Name or '')[:80]}'")
                except Exception as e:
                    print(f"    异常: {e}")
                finally:
                    uia.SetGlobalSearchTimeout(10000)

                # Clipboard test now that we're inside a chat
                print("\n  剪贴板测试 (聊天窗口内):")
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    time.sleep(0.3)
                    # Just try foreground without SetForegroundWindow
                    import pyautogui
                    pyautogui.FAILSAFE = False

                    left, top, right, bottom = chatbox.BoundingRectangle
                    msg_left = left + 30
                    msg_right = left + int((right - left) * 0.5)
                    msg_bottom = bottom - 80

                    cx = (msg_left + msg_right) // 2
                    cy = msg_bottom - 50
                    print(f"  点击 ({cx},{cy})")

                    pyperclip.copy("")
                    pyautogui.click(cx, cy)
                    time.sleep(0.3)
                    pyautogui.hotkey('ctrl', 'a')
                    time.sleep(0.3)
                    pyautogui.hotkey('ctrl', 'c')
                    time.sleep(0.4)

                    content = pyperclip.paste()
                    if content and content.strip():
                        print(f"  剪贴板 ({len(content)} 字符):")
                        for line in content.strip().split('\n')[:15]:
                            print(f"    {line[:200]}")
                    else:
                        print("  剪贴板为空")
                except Exception as e:
                    print(f"  剪贴板异常: {e}")
            else:
                print("  未找到有未读的会话")
        else:
            print("  SessionBox 内无 ListControl")
    except Exception as e:
        print(f"  打开会话测试异常: {e}")
    finally:
        uia.SetGlobalSearchTimeout(10000)

    print("\n" + "=" * 60)
    print("诊断完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
