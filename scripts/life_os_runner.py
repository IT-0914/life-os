#!/usr/bin/env python3
"""
LIFE OS - 統合ランナー
時刻によって朝の処理（デイリーノート生成）または夜の処理（タスクスキャン）を実行する。
- 7:00 JST → morning_daily_generator.py を実行
- 22:00 JST → evening_task_scanner.py を実行
- それ以外の時刻に呼ばれた場合は直近のタスクを実行
"""

import datetime
import subprocess
import sys
import os

JST = datetime.timezone(datetime.timedelta(hours=9))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def run_morning():
    """朝のデイリーノート生成"""
    script = os.path.join(SCRIPT_DIR, "morning_daily_generator.py")
    print(f"[LIFE OS] 朝の処理を開始します: {script}")
    result = subprocess.run(["python3", script], capture_output=False)
    return result.returncode

def run_evening():
    """夜間タスクスキャン"""
    script = os.path.join(SCRIPT_DIR, "evening_task_scanner.py")
    print(f"[LIFE OS] 夜間スキャンを開始します: {script}")
    result = subprocess.run(["python3", script], capture_output=False)
    return result.returncode

def main():
    now = datetime.datetime.now(JST)
    hour = now.hour
    
    print(f"[LIFE OS] 実行時刻: {now.strftime('%Y-%m-%d %H:%M')} JST")
    
    # 引数で強制指定も可能
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        if mode == "morning":
            sys.exit(run_morning())
        elif mode == "evening":
            sys.exit(run_evening())
    
    # 時刻で自動判定
    # 朝: 5:00〜12:00 → デイリーノート生成
    # 夜: 20:00〜24:00 → タスクスキャン
    if 5 <= hour < 12:
        sys.exit(run_morning())
    elif 20 <= hour <= 23:
        sys.exit(run_evening())
    else:
        # デフォルトは朝の処理
        print(f"[LIFE OS] 時刻 {hour}:xx は通常範囲外。デイリーノート生成を実行します。")
        sys.exit(run_morning())

if __name__ == "__main__":
    main()
