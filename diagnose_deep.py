#!/usr/bin/env python3
"""Deep UIA tree scan + clipboard reading without foreground"""
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


def deep_scan(control, max_depth=15):
    """Recursively scan ALL children to find any control with Name or TextControl type"""
    found = []
    total_visited = [0]

    def _scan(c, depth):
        if depth > max_depth:
            return
        try:
            children = c.GetChildren()
            for child in children:
                total_visited[0] += 1
                try:
                    ctype = child.ControlTypeName
                    name = child.Name or ''
                    rect = child.BoundingRectangle
                    w, h = rect.width(), rect.height()

                    if name or ctype == 'TextControl' or ctype == 'ListItemControl':
                        found.append((depth, ctype, name[:120], rect.left, rect.top, rect.bottom, w, h))

                    _scan(child, depth + 1)
                except Exception:
                    pass
        except Exception:
            pass

    _scan(control, 0)
    return found, total_visited[0]


def try_clipboard_via_click(hwnd, chatbox):
    """Try clipboard reading by clicking in chat area WITHOUT foreground"""
    import pyautogui
    pyautogui.FAILSAFE = False

    rect = chatbox.BoundingRectangle
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    print(f"ChatBox: ({left},{top},{right},{bottom})")

    # Message area coordinates (left portion of ChatBox)
    cx = left + 100  # 100px from left edge
    cy = bottom - 150  # 150px from bottom

    # Test 1: Click then Ctrl+A Ctrl+C via pyautogui
    print("\n方法1: pyautogui click + hotkey")
    pyperclip.copy("")
    time.sleep(0.05)
    pyautogui.click(cx, cy)
    time.sleep(0.3)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.3)
    pyautogui.hotkey('ctrl', 'c')
    time.sleep(0.4)
    c = pyperclip.paste()
    if c and c.strip():
        print(f"  成功! ({len(c)} 字符): {c[:200]}")
        return c
    print(f"  空")

    # Test 2: Triple click to select message
    print("\n方法2: triple click")
    pyperclip.copy("")
    time.sleep(0.05)
    for _ in range(3):
        pyautogui.click(cx, cy - 50)
        time.sleep(0.08)
    time.sleep(0.1)
    pyautogui.hotkey('ctrl', 'c')
    time.sleep(0.3)
    c = pyperclip.paste()
    if c and c.strip():
        print(f"  成功! ({len(c)} 字符): {c[:200]}")
        return c
    print(f"  空")

    # Test 3: Click + drag select + Ctrl+C
    print("\n方法3: drag select")
    pyperclip.copy("")
    time.sleep(0.05)
    pyautogui.moveTo(cx, cy - 200)
    time.sleep(0.1)
    pyautogui.mouseDown()
    time.sleep(0.05)
    pyautogui.moveTo(cx + 200, cy)
    time.sleep(0.1)
    pyautogui.mouseUp()
    time.sleep(0.1)
    pyautogui.hotkey('ctrl', 'c')
    time.sleep(0.3)
    c = pyperclip.paste()
    if c and c.strip():
        print(f"  成功! ({len(c)} 字符): {c[:200]}")
        return c
    print(f"  空")

    # Test 4: Use UIA SendKeys instead of pyautogui hotkey
    print("\n方法4: UIA SendKeys Ctrl+A")
    pyperclip.copy("")
    time.sleep(0.05)
    pyautogui.click(cx, cy)
    time.sleep(0.3)
    wx_window = uia.WindowControl(ClassName='WeChatMainWndForPC', searchDepth=1)
    wx_window.SendKeys('{Ctrl}a')
    time.sleep(0.3)
    wx_window.SendKeys('{Ctrl}c')
    time.sleep(0.4)
    c = pyperclip.paste()
    if c and c.strip():
        print(f"  成功! ({len(c)} 字符): {c[:200]}")
        return c
    print(f"  空")

    return None


def find_messages_in_tree(chatbox):
    """Look for message-like structures in the UIA tree"""
    print("\n搜索深度消息控件...")
    # Check if there's a ListControl with different name
    for depth in [3, 5, 8, 10, 12, 15, 20]:
        try:
            uia.SetGlobalSearchTimeout(1000)
            # Try ListControl
            lc = chatbox.ListControl(searchDepth=depth)
            if lc.Exists(0.15):
                name = lc.Name or ''
                cnt = len(lc.GetChildren())
                print(f"  ListControl depth={depth}: Name='{name[:60]}' items={cnt}")
                if cnt > 0:
                    for i, item in enumerate(lc.GetChildren()[:3]):
                        it_name = item.Name or ''
                        it_type = item.ControlTypeName
                        print(f"    [{i}] {it_type} '{it_name[:80]}'")
        except Exception:
            pass

        try:
            # Try any ListItemControl at this depth
            uia.SetGlobalSearchTimeout(1000)
            li = chatbox.ListItemControl(searchDepth=depth)
            if li.Exists(0.15):
                name = li.Name or ''
                print(f"  ListItemControl depth={depth}: Name='{name[:60]}'")
        except Exception:
            pass
        finally:
            uia.SetGlobalSearchTimeout(10000)


def main():
    print("=" * 60)
    print("Deep UIA Scan + Clipboard Test")
    print("=" * 60)

    hwnd = win32gui.FindWindow('WeChatMainWndForPC', None)
    if not hwnd:
        print("未找到微信窗口!")
        return

    print(f"WeChat HWND={hwnd}, 标题='{win32gui.GetWindowText(hwnd)}'")

    wx_window = uia.WindowControl(ClassName='WeChatMainWndForPC', searchDepth=1)
    children = wx_window.GetChildren()
    main1 = [i for i in children if not i.ClassName][0]
    main2 = main1.GetFirstChildControl()
    inner = main2.GetChildren()
    sessionbox = inner[1]
    chatbox = inner[2]

    # 1. Deep scan ChatBox
    print("\n===== ChatBox 深度扫描 (max_depth=15) =====")
    found, total = deep_scan(chatbox, max_depth=12)
    print(f"总计扫描: {total} 个控件, 有Name的: {len(found)}")
    if found:
        for depth, ctype, name, left, top, bottom, w, h in found[:30]:
            print(f"  [d={depth}] {ctype} '{name[:80]}' ({left},{top},{bottom})[{w}x{h}]")
    else:
        print("  未找到任何有名称的控件!")

    # 2. Search for message list
    find_messages_in_tree(chatbox)

    # 3. Deep scan SessionBox
    print("\n===== SessionBox 深度扫描 =====")
    found2, total2 = deep_scan(sessionbox, max_depth=12)
    print(f"总计扫描: {total2} 个控件, 有Name的: {len(found2)}")
    for depth, ctype, name, left, top, bottom, w, h in found2[:20]:
        print(f"  [d={depth}] {ctype} '{name[:80]}' ({left},{top},{bottom})[{w}x{h}]")

    # 4. Try to open a chat
    print("\n===== 打开聊天 =====")
    try:
        uia.SetGlobalSearchTimeout(3000)
        # First click chat icon to ensure we're in list
        navbox = inner[0]
        chat_icon = navbox.ButtonControl(Name='聊天')
        if chat_icon.Exists(0.3):
            chat_icon.DoubleClick(simulateMove=False)
            time.sleep(0.5)

        lc = sessionbox.ListControl(Name='会话', searchDepth=8)
        if lc.Exists(0.5):
            items = lc.GetChildren()
            for item in items:
                name = item.Name or ''
                if '新消息' in name and 'JZ' in name:
                    print(f"点击: '{name[:60]}'")
                    item.Click(simulateMove=False)
                    time.sleep(1.5)
                    break
    except Exception as e:
        print(f"打开聊天异常: {e}")
    finally:
        uia.SetGlobalSearchTimeout(10000)

    print(f"点击后窗口标题: '{win32gui.GetWindowText(hwnd)}'")

    # 5. Re-scan ChatBox after opening chat
    print("\n===== ChatBox 深度扫描 (打开聊天后) =====")
    found3, total3 = deep_scan(chatbox, max_depth=12)
    print(f"总计扫描: {total3} 个控件, 有Name的: {len(found3)}")
    if found3:
        for depth, ctype, name, left, top, bottom, w, h in found3[:30]:
            print(f"  [d={depth}] {ctype} '{name[:80]}' ({left},{top},{bottom})[{w}x{h}]")
    else:
        print("  仍然未找到任何有名称的控件!")

    # 6. Try clipboard
    print("\n===== 剪贴板测试 =====")
    result = try_clipboard_via_click(hwnd, chatbox)

    if result:
        print("\n*** 剪贴板成功! 消息可读取 ***")
    else:
        print("\n*** 剪贴板失败，消息无法通过剪贴板读取 ***")

    print("\n" + "=" * 60)
    print("诊断完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
