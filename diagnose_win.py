#!/usr/bin/env python3
"""Check WeChat window state and try alternative activation methods"""
import sys
import time

PKG_DIR = r"C:\Users\ohmyc\AppData\Roaming\Python\Python312\site-packages\wxauto"
sys.path.insert(0, PKG_DIR)

import uiautomation as uia
import win32gui
import win32con
import win32process
import ctypes

def get_window_info(hwnd):
    """Get detailed window info"""
    if not win32gui.IsWindow(hwnd):
        return {"valid": False}
    info = {
        "valid": True,
        "title": win32gui.GetWindowText(hwnd),
        "class": win32gui.GetClassName(hwnd),
        "rect": win32gui.GetWindowRect(hwnd),
        "visible": win32gui.IsWindowVisible(hwnd),
        "iconic": win32gui.IsIconic(hwnd),
        "enabled": win32gui.IsWindowEnabled(hwnd),
    }
    # Get thread/process
    tid, pid = win32process.GetWindowThreadProcessId(hwnd)
    info["pid"] = pid
    info["tid"] = tid
    # Window style
    info["style"] = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    info["exstyle"] = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    return info

def main():
    print("=" * 60)
    print("WeChat 窗口状态诊断")
    print("=" * 60)

    # Find ALL WeChat windows
    wechat_windows = []
    def enum_callback(hwnd, _):
        if win32gui.IsWindow(hwnd):
            cls = win32gui.GetClassName(hwnd)
            if 'WeChat' in cls:
                wechat_windows.append(hwnd)
        return True
    win32gui.EnumWindows(enum_callback, None)

    print(f"\n找到 {len(wechat_windows)} 个微信窗口:")
    for hwnd in wechat_windows:
        info = get_window_info(hwnd)
        for k, v in info.items():
            if k == 'style':
                print(f"  {k}: 0x{v:08X}")
            elif k == 'exstyle':
                print(f"  {k}: 0x{v:08X}")
            else:
                print(f"  {k}: {v}")
        print()

    # Try activation via different methods
    main_hwnd = win32gui.FindWindow('WeChatMainWndForPC', None)
    if main_hwnd:
        print(f"Main WeChat HWND: {main_hwnd}")
        info = get_window_info(main_hwnd)
        print(f"  Visible: {info.get('visible')}, Iconic: {info.get('iconic')}, Enabled: {info.get('enabled')}")

        # Check if we're on same desktop
        cur_desktop = ctypes.windll.user32.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId())
        win_desktop = ctypes.windll.user32.GetThreadDesktop(info.get('tid', 0))
        print(f"  Desktop: current={cur_desktop} window={win_desktop}")

        # Try UIA SetFocus
        print("\n尝试 UIA SetFocus...")
        try:
            wx = uia.WindowControl(ClassName='WeChatMainWndForPC', searchDepth=1)
            wx.SetFocus()
            time.sleep(0.3)
            print(f"  SetFocus 后前景窗口: {win32gui.GetWindowText(win32gui.GetForegroundWindow())}")
        except Exception as e:
            print(f"  SetFocus 异常: {e}")

        # Try SwitchToThisWindow
        print("\n尝试 SwitchToThisWindow...")
        try:
            wx.SwitchToThisWindow()
            time.sleep(0.3)
            print(f"  后前景窗口: {win32gui.GetWindowText(win32gui.GetForegroundWindow())}")
        except Exception as e:
            print(f"  SwitchToThisWindow 异常: {e}")

        # Try UIA Click to activate
        print("\n尝试 Click 窗口标题栏...")
        try:
            uia.SetGlobalSearchTimeout(2000)
            title_bar = wx.TextControl(Name='微信', searchDepth=3)
            if title_bar.Exists(0.3):
                title_bar.Click(simulateMove=False)
                time.sleep(0.5)
                print(f"  Click 后前景: {win32gui.GetWindowText(win32gui.GetForegroundWindow())}")
            else:
                print("  找不到标题 TextControl")
        except Exception as e:
            print(f"  Click 异常: {e}")
        finally:
            uia.SetGlobalSearchTimeout(10000)

        # Try alt-tab approach using keyboard
        print("\n尝试 Alt+Tab 切换...")
        try:
            import pyautogui
            pyautogui.FAILSAFE = False
            pyautogui.keyDown('alt')
            time.sleep(0.1)
            pyautogui.press('tab')
            time.sleep(0.1)
            pyautogui.keyUp('alt')
            time.sleep(0.5)
            fg = win32gui.GetForegroundWindow()
            print(f"  Alt+Tab 后前景: {win32gui.GetWindowText(fg)}")
        except Exception as e:
            print(f"  Alt+Tab 异常: {e}")

        # Final state
        time.sleep(1)
        print(f"\n最终前景窗口: {win32gui.GetWindowText(win32gui.GetForegroundWindow())}")
        print(f"微信窗口标题: {win32gui.GetWindowText(main_hwnd)}")

    print("\n" + "=" * 60)
    print("诊断完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
