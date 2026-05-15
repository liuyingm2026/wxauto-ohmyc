@echo off
chcp 65001 >nul
echo ============================================
echo wxauto → Hermes qljk 桥接启动
echo ============================================
echo.
echo 确保:
echo   1. 微信 PC 客户端已登录
echo   2. Hermes qljk gateway 运行中 (:8647)
echo.
"C:\Program Files\Python312\python.exe" C:\Users\ohmyc\.openclaw\workspace\wxauto_bridge\wxauto_bridge.py
pause
