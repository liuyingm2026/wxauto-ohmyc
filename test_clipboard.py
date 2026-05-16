#!/usr/bin/env python3
"""Test clipboard reading using pyautogui click + UIA SendKeys (no hotkey, no foreground needed)"""
import sys
import time

PKG_DIR = r"C:\Users\ohmyc\AppData\Roaming\Python\Python312\site-packages\wxauto"
sys.path.insert(0, PKG_DIR)

import uiautomation as uia
import win32gui
import pyperclip

uia.SetGlobalSearchTimeout(10)


def main():
    print("=" * 60)
    print("Clipboard test: pyautogui click + UIA SendKeys")
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
    navbox = inner[0]
    sessionbox = inner[1]
    chatbox = inner[2]

    rect = chatbox.BoundingRectangle
    msg_left = rect.left + 30
    msg_right = rect.left + int((rect.right - rect.left) * 0.5)
    msg_bottom = rect.bottom - 50
    cx = (msg_left + msg_right) // 2
    cy = msg_bottom - 100

    print(f"消息区域点击: ({cx},{cy})")

    # Open a chat with unread first
    print("\nStep 1: 打开有未读的聊天...")
    try:
        uia.SetGlobalSearchTimeout(3000)
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
                    print(f"  点击: '{name[:60]}'")
                    item.Click(simulateMove=False)
                    time.sleep(2.0)
                    break
    except Exception as e:
        print(f"  异常: {e}")
    finally:
        uia.SetGlobalSearchTimeout(10000)

    print(f"  当前标题: '{win32gui.GetWindowText(hwnd)}'")

    # Step 2: Click in chat area using pyautogui (absolute coords, no foreground needed)
    print("\nStep 2: pyautogui click in chat area...")
    import pyautogui
    pyautogui.FAILSAFE = False

    pyautogui.click(cx, cy)
    time.sleep(0.5)
    print("  Click done")

    # Step 3: Use UIA SendKeys for Ctrl+A (targets WeChat window specifically)
    print("\nStep 3: UIA SendKeys Ctrl+A...")
    try:
        wx_window.SendKeys('{Ctrl}a', waitTime=0.3)
        time.sleep(0.3)
        print("  Ctrl+A sent")
    except Exception as e:
        print(f"  Ctrl+A 异常: {e}")

    # Step 4: UIA SendKeys for Ctrl+C
    print("\nStep 4: UIA SendKeys Ctrl+C...")
    try:
        wx_window.SendKeys('{Ctrl}c', waitTime=0.3)
        time.sleep(0.5)
        print("  Ctrl+C sent")
    except Exception as e:
        print(f"  Ctrl+C 异常: {e}")

    # Step 5: Read clipboard
    print("\nStep 5: Read clipboard...")
    try:
        content = pyperclip.paste()
        if content and content.strip():
            print(f"  *** SUCCESS! ({len(content)} 字符) ***")
            lines = content.strip().split('\n')
            for i, line in enumerate(lines[:20]):
                print(f"  [{i}] {line[:200]}")
        else:
            print("  Clipboard is EMPTY")
            # Try again with more delay
            time.sleep(1.0)
            content = pyperclip.paste()
            if content and content.strip():
                print(f"  Retry success! ({len(content)} 字符)")
                for i, line in enumerate(content.strip().split('\n')[:10]):
                    print(f"  [{i}] {line[:200]}")
            else:
                print("  Still empty after retry")
    except Exception as e:
        print(f"  Clipboard 异常: {e}")

    # Step 6: Alternative - click different positions
    print("\n" + "=" * 60)
    print("Alternative: click at multiple heights")
    print("=" * 60)
    for offset in [0, -80, -160, -240]:
        pyperclip.copy("")
        time.sleep(0.05)
        pyautogui.click(cx, cy + offset)
        time.sleep(0.3)
        try:
            wx_window.SendKeys('{Ctrl}a', waitTime=0.3)
            time.sleep(0.3)
            wx_window.SendKeys('{Ctrl}c', waitTime=0.3)
            time.sleep(0.5)
            content = pyperclip.paste()
            if content and content.strip():
                print(f"  offset={offset}: SUCCESS ({len(content)} 字符)")
                for line in content.strip().split('\n')[:5]:
                    print(f"    {line[:150]}")
                return
            else:
                print(f"  offset={offset}: empty")
        except Exception as e:
            print(f"  offset={offset}: 异常 {e}")

    print("\n*** Clipboard reading FAILED for all positions ***")
    print("=" * 60)


if __name__ == "__main__":
    main()
