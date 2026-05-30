#!/usr/bin/env python3
"""
LIFE OS - 夜間タスクスキャンスクリプト（22:00 JST実行）
当日のDAILY DBページのTODAYセクションをスキャンし：
1. チェック済み（完了）→ TASK DBにSTATUS=DONEで登録
2. 未チェック（未完了）→ TASK DBにSTATUS=TODO, MIGRATED=falseで登録
   → 翌朝のデイリーノートに自動引き継ぎ
"""

import subprocess
import json
import datetime
import re
import glob
import sys

# ============================================================
# 設定
# ============================================================
DAILY_DS_ID = "f9b89321-f903-4167-b022-0787096ea6f3"
TASK_DS_ID  = "47ae00b4-956c-4fa4-a786-af39a5b33067"
PROJ_DS_ID  = "eadd59d6-f0d5-4356-bc13-76aeab13c0ec"

JST = datetime.timezone(datetime.timedelta(hours=9))


# ============================================================
# ユーティリティ
# ============================================================
def mcp_call(tool: str, server: str, input_dict: dict) -> dict:
    """MCP CLIを呼び出して最新の結果ファイルを返す"""
    cmd = [
        "manus-mcp-cli", "tool", "call", tool,
        "--server", server,
        "--input", json.dumps(input_dict, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    
    pattern = f"/home/ubuntu/.mcp/tool-results/*{tool.replace('_', '-')}*.json"
    files = sorted(glob.glob(pattern), reverse=True)
    # notion系はnotion-で始まるファイル
    if not files:
        files = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*.json"), reverse=True)
    
    if files:
        try:
            with open(files[0]) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# ============================================================
# 1. 当日のDAILYページを取得
# ============================================================
def get_today_daily_page(today: datetime.date) -> dict:
    """当日のDAILYページを検索して返す"""
    weekdays_ja = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = weekdays_ja[today.weekday()]
    title = f"{today.strftime('%Y-%m-%d')}（{weekday}）"
    
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-search",
        "--server", "notion",
        "--input", json.dumps({"query": title}, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    
    files = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-search*.json"), reverse=True)
    if not files:
        return {}
    
    try:
        with open(files[0]) as f:
            data = json.load(f)
        results = data.get("results", [])
        for r in results:
            if title in r.get("title", ""):
                return r
    except Exception as e:
        print(f"[WARN] DAILYページ検索エラー: {e}")
    return {}


# ============================================================
# 2. DAILYページのTODAYセクションをスキャン
# ============================================================
def scan_today_tasks(page_url: str) -> tuple[list[dict], list[dict]]:
    """
    DAILYページのTODAYセクションをスキャン
    Returns: (completed_tasks, pending_tasks)
    """
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-fetch",
        "--server", "notion",
        "--input", json.dumps({"url": page_url}, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    
    files = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-fetch*.json"), reverse=True)
    if not files:
        return [], []
    
    try:
        with open(files[0]) as f:
            data = json.load(f)
        result_text = data.get("result", "")
    except Exception:
        return [], []
    
    completed = []
    pending = []
    in_today_section = False
    
    for line in result_text.split("\n"):
        # TODAYセクションの開始を検出
        if "## ✅ TODAY" in line or "## TODAY" in line:
            in_today_section = True
            continue
        
        # 次のセクションに入ったら終了
        if in_today_section and line.startswith("## ") and "TODAY" not in line:
            in_today_section = False
            continue
        
        if not in_today_section:
            continue
        
        # チェックボックス行を検出
        # 完了: - [x] または - [X]
        # 未完了: - [ ]
        
        # 完了タスク
        done_match = re.match(r'^[-*]\s+\[x\]\s+(.+)$', line, re.IGNORECASE)
        if done_match:
            task_text = done_match.group(1).strip()
            # 🔁 や [PJ: ...] プレフィックスを除去
            task_name = re.sub(r'^🔁\s+', '', task_text)
            task_name = re.sub(r'^\[PJ:[^\]]+\]\s+', '', task_name)
            # リンク形式 [name](url) をパース
            link_match = re.match(r'\[([^\]]+)\]\(([^)]+)\)', task_name)
            if link_match:
                task_name = link_match.group(1)
                task_url = link_match.group(2)
            else:
                task_url = ""
            
            # PJを抽出
            pj_match = re.search(r'\[PJ:\s*([^\]]+)\]', task_text)
            project_name = pj_match.group(1).strip() if pj_match else ""
            
            if task_name and task_name != "（タスクを追加）":
                completed.append({
                    "name": task_name,
                    "url": task_url,
                    "project": project_name,
                    "is_migration": "🔁" in task_text
                })
        
        # 未完了タスク
        todo_match = re.match(r'^[-*]\s+\[ \]\s+(.+)$', line)
        if todo_match:
            task_text = todo_match.group(1).strip()
            task_name = re.sub(r'^🔁\s+', '', task_text)
            task_name = re.sub(r'^\[PJ:[^\]]+\]\s+', '', task_name)
            link_match = re.match(r'\[([^\]]+)\]\(([^)]+)\)', task_name)
            if link_match:
                task_name = link_match.group(1)
                task_url = link_match.group(2)
            else:
                task_url = ""
            
            pj_match = re.search(r'\[PJ:\s*([^\]]+)\]', task_text)
            project_name = pj_match.group(1).strip() if pj_match else ""
            
            if task_name and task_name != "（タスクを追加）":
                pending.append({
                    "name": task_name,
                    "url": task_url,
                    "project": project_name,
                    "is_migration": "🔁" in task_text
                })
    
    return completed, pending


# ============================================================
# 3. TASK DBにタスクを登録・更新
# ============================================================
def find_task_in_db(task_name: str) -> str:
    """TASK DBで同名タスクを検索してURLを返す（なければ空文字）"""
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-search",
        "--server", "notion",
        "--input", json.dumps({"query": task_name}, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    
    files = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-search*.json"), reverse=True)
    if not files:
        return ""
    
    try:
        with open(files[0]) as f:
            data = json.load(f)
        for r in data.get("results", []):
            if r.get("title", "").strip() == task_name.strip():
                return r.get("url", "")
    except Exception:
        pass
    return ""


def register_task_to_db(task: dict, status: str, daily_page_url: str):
    """タスクをTASK DBに登録または更新"""
    
    # 既存タスクがあれば更新
    if task.get("url"):
        # 既存ページのSTATUSを更新
        cmd = [
            "manus-mcp-cli", "tool", "call", "notion-update-page",
            "--server", "notion",
            "--input", json.dumps({
                "page_url": task["url"],
                "properties": {
                    "STATUS": status,
                    "MIGRATED": "__NO__" if status == "TODO" else "__YES__"
                }
            }, ensure_ascii=False)
        ]
        subprocess.run(cmd, capture_output=True, text=True)
        print(f"  [更新] {task['name']} → {status}")
        return
    
    # 新規タスクをTASK DBに作成
    properties = {
        "TASK": task["name"],
        "STATUS": status,
        "MIGRATED": "__NO__" if status == "TODO" else "__YES__"
    }
    if daily_page_url:
        properties["DAILY"] = [daily_page_url]
    
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-create-pages",
        "--server", "notion",
        "--input", json.dumps({
            "parent": {"data_source_id": TASK_DS_ID},
            "pages": [{"properties": properties}]
        }, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    print(f"  [新規] {task['name']} → {status}")


# ============================================================
# メイン処理
# ============================================================
def main():
    now = datetime.datetime.now(JST)
    today = now.date()
    
    print(f"=== LIFE OS 夜間タスクスキャン {today} ===")
    
    # 1. 当日のDAILYページを取得
    print("[1/4] 当日のDAILYページを検索中...")
    daily_page = get_today_daily_page(today)
    
    if not daily_page:
        print("[WARN] 当日のDAILYページが見つかりません。スキップします。")
        sys.exit(0)
    
    daily_url = daily_page.get("url", "")
    print(f"      → {daily_page.get('title', '')} ({daily_url})")
    
    # 2. TODAYセクションをスキャン
    print("[2/4] TODAYセクションをスキャン中...")
    completed_tasks, pending_tasks = scan_today_tasks(daily_url)
    print(f"      → 完了: {len(completed_tasks)}件 / 未完了: {len(pending_tasks)}件")
    
    # 3. 完了タスクをTASK DBに登録（STATUS=DONE）
    if completed_tasks:
        print("[3/4] 完了タスクをTASK DBに登録中...")
        for task in completed_tasks:
            register_task_to_db(task, "DONE", daily_url)
    else:
        print("[3/4] 完了タスクなし")
    
    # 4. 未完了タスクをTASK DBに登録（STATUS=TODO, MIGRATED=false）
    if pending_tasks:
        print("[4/4] 未完了タスクをTASK DBに登録中（翌日引き継ぎ）...")
        for task in pending_tasks:
            register_task_to_db(task, "TODO", daily_url)
    else:
        print("[4/4] 未完了タスクなし")
    
    print(f"\n✅ 完了: 夜間スキャン終了 ({len(completed_tasks)}件完了, {len(pending_tasks)}件引き継ぎ)")


if __name__ == "__main__":
    main()
