#!/usr/bin/env python3
"""
LIFE OS - 統合ランナー
毎朝7:00 JSTに実行。morning_daily_generator.py を呼び出す。
夜間スキャンは廃止。前日スキャン処理は morning_daily_generator.py に統合済み。
"""

import subprocess
import sys

def main():
    result = subprocess.run(
        [sys.executable, "/home/ubuntu/life_os_scripts/morning_daily_generator.py"],
        capture_output=False
    )
    sys.exit(result.returncode)

if __name__ == "__main__":
    main()
