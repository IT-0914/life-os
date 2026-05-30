#!/usr/bin/env python3
"""
LIFE OS - 朝7:00 統合デイリーノート生成スクリプト
毎朝7:00 JSTに実行。以下を順次処理：
0. 前日のデイリーノートのTODAYセクションをスキャン → TASK DBに同期
1. TASK DBから未完了タスク（MIGRATION）を取得
2. PROJECT DBからACTIVEプロジェクトのタスクと進捗を取得
3. Googleカレンダーから当日イベントを取得（ミーティングはサブページ生成）
4. DAILY DBに当日のデイリーノートを作成
"""

import subprocess
import json
import datetime
import re
import sys

# ============================================================
# 設定
# ============================================================
DAILY_DS_ID  = "f9b89321-f903-4167-b022-0787096ea6f3"
TASK_DS_ID   = "47ae00b4-956c-4fa4-a786-af39a5b33067"
PROJ_DS_ID   = "eadd59d6-f0d5-4356-bc13-76aeab13c0ec"
DAILY_DB_ID  = "77de58c499d14be9817ebd539c551eb0"
DAILY_DB_URL = "https://www.notion.so/77de58c499d14be9817ebd539c551eb0"
TASK_DB_URL  = "https://www.notion.so/6135d9e113d64fba81c4d12d3ac24bfe"
PROJ_DB_URL  = "https://www.notion.so/ae6d2424256c47249c5cdccf644560bc"
DB_HUB_URL   = "https://www.notion.so/370200b3cc70817d9fcad1c4190f79fe"

JST = datetime.timezone(datetime.timedelta(hours=9))

# ============================================================
# ユーティリティ
# ============================================================
def mcp_call(tool: str, server: str, input_dict: dict) -> dict:
    """MCP CLIを呼び出してJSONを返す"""
    cmd = [
        "manus-mcp-cli", "tool", "call", tool,
        "--server", server,
        "--input", json.dumps(input_dict, ensure_ascii=False)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    # 結果ファイルパスを取得
    for line in result.stdout.split("\n"):
        if "Tool execution result:" in line:
            break
    # stdout から JSON を抽出
    output = result.stdout
    # "Tool execution result:" 以降を取得
    match = re.search(r'Tool execution result:\s*(\{.*\}|\[.*\])', output, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return {"raw": output, "error": result.stderr}


def mcp_call_raw(tool: str, server: str, input_dict: dict) -> str:
    """MCP CLIを呼び出して生テキストを返す"""
    cmd = [
        "manus-mcp-cli", "tool", "call", tool,
        "--server", server,
        "--input", json.dumps(input_dict, ensure_ascii=False)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout


# ============================================================
# 1. 未完了タスクの取得（MIGRATION）
# ============================================================
def get_pending_tasks() -> list[dict]:
    """STATUS=TODO かつ MIGRATED=false のタスクを取得"""
    raw = mcp_call_raw("notion-fetch", "notion", {
        "url": f"collection://{TASK_DS_ID}",
        "query": {
            "filter": {
                "and": [
                    {"property": "STATUS", "select": {"equals": "TODO"}},
                    {"property": "MIGRATED", "checkbox": {"equals": False}}
                ]
            }
        }
    })
    
    # 結果からタスクを抽出（シンプルなパース）
    tasks = []
    # notion-fetchはMarkdown形式で返すので、行ごとにパース
    lines = raw.split("\n")
    for line in lines:
        if "| " in line and "TASK" not in line and "---" not in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if parts and len(parts) >= 2:
                task_name = parts[0]
                if task_name and task_name != "TASK":
                    tasks.append({"name": task_name, "url": ""})
    return tasks


def get_pending_tasks_v2() -> list[dict]:
    """TASK DBからTODO・MIGRATED=falseのタスクをMCP経由で取得"""
    import os
    import glob
    
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-fetch",
        "--server", "notion",
        "--input", json.dumps({
            "url": f"collection://{TASK_DS_ID}"
        }, ensure_ascii=False)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    # 最新の結果ファイルを読む
    files = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-fetch*.json"), reverse=True)
    if not files:
        return []
    
    try:
        with open(files[0]) as f:
            data = json.load(f)
        
        tasks = []
        result_text = data.get("result", "")
        
        # テーブル行からタスクを抽出
        for line in result_text.split("\n"):
            if "| " in line and "TODO" in line and "__NO__" in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if parts:
                    # URLからページIDを取得
                    url_match = re.search(r'https://www\.notion\.so/([a-f0-9]+)', line)
                    page_url = url_match.group(0) if url_match else ""
                    task_name = parts[0].replace("[", "").replace("]", "")
                    # リンク形式 [name](url) をパース
                    link_match = re.match(r'\[([^\]]+)\]\(([^)]+)\)', parts[0])
                    if link_match:
                        task_name = link_match.group(1)
                        page_url = link_match.group(2)
                    tasks.append({"name": task_name, "url": page_url})
        return tasks
    except Exception as e:
        print(f"[WARN] タスク取得エラー: {e}")
        return []


# ============================================================
# 2. プロジェクト情報の取得
# ============================================================
def get_active_projects() -> list[dict]:
    """STATUS=ACTIVEのプロジェクトとそのTODOタスクを取得"""
    import glob
    
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-fetch",
        "--server", "notion",
        "--input", json.dumps({"url": f"collection://{PROJ_DS_ID}"}, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    
    files = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-fetch*.json"), reverse=True)
    if not files:
        return []
    
    try:
        with open(files[0]) as f:
            data = json.load(f)
        
        projects = []
        result_text = data.get("result", "")
        
        for line in result_text.split("\n"):
            if "| " in line and "ACTIVE" in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 4:
                    link_match = re.match(r'\[([^\]]+)\]\(([^)]+)\)', parts[0])
                    name = link_match.group(1) if link_match else parts[0]
                    url = link_match.group(2) if link_match else ""
                    
                    # PROGRESS と NEXT ACTION を探す
                    progress = ""
                    next_action = ""
                    for p in parts:
                        if re.match(r'^\d+(\.\d+)?$', p):
                            progress = f"{int(float(p))}%"
                        elif p and p not in ["ACTIVE", name] and not re.match(r'^https?://', p):
                            if len(p) > 3:
                                next_action = p
                    
                    projects.append({
                        "name": name,
                        "url": url,
                        "progress": progress or "0%",
                        "next_action": next_action or "（未設定）"
                    })
        return projects
    except Exception as e:
        print(f"[WARN] プロジェクト取得エラー: {e}")
        return []


def get_project_tasks(project_url: str) -> list[dict]:
    """プロジェクトに紐付くTODOタスクを取得"""
    import glob
    
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-fetch",
        "--server", "notion",
        "--input", json.dumps({"url": project_url}, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    
    files = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-fetch*.json"), reverse=True)
    if not files:
        return []
    
    try:
        with open(files[0]) as f:
            data = json.load(f)
        result_text = data.get("result", "")
        
        tasks = []
        for line in result_text.split("\n"):
            if "TODO" in line and "| " in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if parts:
                    link_match = re.match(r'\[([^\]]+)\]\(([^)]+)\)', parts[0])
                    name = link_match.group(1) if link_match else parts[0]
                    url = link_match.group(2) if link_match else ""
                    tasks.append({"name": name, "url": url})
        return tasks
    except Exception as e:
        return []


# ============================================================
# 3. Googleカレンダーイベントの取得
# ============================================================
def get_today_events(today: datetime.date) -> list[dict]:
    """当日のGoogleカレンダーイベントを取得"""
    import glob
    
    time_min = f"{today.isoformat()}T00:00:00+09:00"
    time_max = f"{today.isoformat()}T23:59:59+09:00"
    
    cmd = [
        "manus-mcp-cli", "tool", "call", "google_calendar_search_events",
        "--server", "google-calendar",
        "--input", json.dumps({
            "time_min": time_min,
            "time_max": time_max,
            "max_results": 20,
            "calendar_id": "primary"
        }, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    
    files = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*google_calendar*.json"), reverse=True)
    if not files:
        return []
    
    try:
        with open(files[0]) as f:
            data = json.load(f)
        
        events = []
        items = data.get("events", data.get("items", []))
        if isinstance(items, list):
            for item in items:
                start = item.get("start", {})
                start_time = start.get("dateTime", start.get("date", ""))
                summary = item.get("summary", "（タイトルなし）")
                event_id = item.get("id", "")
                
                # 時刻フォーマット
                if "T" in start_time:
                    dt = datetime.datetime.fromisoformat(start_time)
                    time_str = dt.strftime("%H:%M")
                else:
                    time_str = "終日"
                
                events.append({
                    "time": time_str,
                    "summary": summary,
                    "id": event_id,
                    "is_meeting": any(kw in summary for kw in ["ミーティング", "MTG", "meeting", "Meeting", "会議", "打ち合わせ", "面談", "call", "Call"])
                })
        
        # 時刻順にソート
        events.sort(key=lambda x: x["time"] if x["time"] != "終日" else "00:00")
        return events
    except Exception as e:
        print(f"[WARN] カレンダー取得エラー: {e}")
        return []


# ============================================================
# 4. ミーティングサブページの作成
# ============================================================
def create_meeting_page(daily_page_id: str, event: dict, today: datetime.date) -> str:
    """ミーティングのサブページを作成してURLを返す"""
    import glob
    
    title = f"📎 {event['summary']} ({today.strftime('%Y-%m-%d')} {event['time']})"
    content = f"""**日時:** {today.strftime('%Y-%m-%d')} {event['time']}
**参加者:** 
**アジェンダ:** 

---

## AI Meeting Note

*ここにAI Meeting Noteを貼り付け*

---

## 決定事項


## Next Action

"""
    
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-create-pages",
        "--server", "notion",
        "--input", json.dumps({
            "parent": {"page_id": daily_page_id},
            "pages": [{
                "properties": {"title": title},
                "icon": "📎",
                "content": content
            }]
        }, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    
    files = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-create-pages*.json"), reverse=True)
    if not files:
        return ""
    
    try:
        with open(files[0]) as f:
            data = json.load(f)
        pages = data.get("pages", [])
        if pages:
            return pages[0].get("url", "")
    except Exception:
        pass
    return ""


# ============================================================
# 5. デイリーノートのコンテンツ生成
# ============================================================
def build_daily_content(
    today: datetime.date,
    pending_tasks: list[dict],
    projects: list[dict],
    events: list[dict],
    meeting_links: dict  # {event_summary: page_url}
) -> str:
    """デイリーノートのMarkdownコンテンツを生成"""
    
    weekdays_ja = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = weekdays_ja[today.weekday()]
    is_friday = today.weekday() == 4
    
    lines = []

    # ── ナビゲーション ──
    lines.append(f"[✅ TASK]({TASK_DB_URL})　　[🚀 PROJECT]({PROJ_DB_URL})　　[📅 DAILY一覧]({DAILY_DB_URL})　　[🗄 DB HUB]({DB_HUB_URL})")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── SCHEDULE ──
    lines.append("## 📅 SCHEDULE")
    lines.append("")
    if events:
        for ev in events:
            if ev["summary"] in meeting_links and meeting_links[ev["summary"]]:
                lines.append(f"- {ev['time']}  [{ev['summary']}]({meeting_links[ev['summary']]})")
            else:
                lines.append(f"- {ev['time']}  {ev['summary']}")
    else:
        lines.append("- （予定なし）")
    lines.append("")
    
    # ── TODAY ──
    lines.append("## ✅ TODAY")
    lines.append("")
    
    # 引き継ぎタスク
    if pending_tasks:
        for task in pending_tasks:
            if task.get("url"):
                lines.append(f"- 🔁 [ ] [{task['name']}]({task['url']})")
            else:
                lines.append(f"- 🔁 [ ] {task['name']}")
    
    # PJタスク
    for proj in projects:
        if proj.get("tasks"):
            for task in proj["tasks"]:
                if task.get("url"):
                    lines.append(f"- [PJ: {proj['name']}] [ ] [{task['name']}]({task['url']})")
                else:
                    lines.append(f"- [PJ: {proj['name']}] [ ] {task['name']}")
    
    if not pending_tasks and not any(p.get("tasks") for p in projects):
        lines.append("- [ ] （タスクを追加）")
    
    lines.append("")
    
    # ── PROJECT STATUS ──
    lines.append("## 🚀 PROJECT STATUS")
    lines.append("")
    if projects:
        for proj in projects:
            progress = proj.get("progress", "0%")
            next_action = proj.get("next_action", "（未設定）")
            if proj.get("url"):
                lines.append(f"- [{proj['name']}]({proj['url']})  {progress}  → 次: {next_action}")
            else:
                lines.append(f"- {proj['name']}  {progress}  → 次: {next_action}")
    else:
        lines.append("- （進行中のプロジェクトなし）")
    lines.append("")
    
    # ── LOG ──
    lines.append("## 📝 LOG")
    lines.append("")
    lines.append("（自由記述 — ミーティングメモ・気づき・感情・何でも）")
    lines.append("")
    
    # ── WEEKLY REVIEW（金曜のみ）──
    if is_friday:
        lines.append("---")
        lines.append("")
        lines.append("## 🌙 WEEKLY REVIEW")
        lines.append("")
        lines.append("**今週どうだった？**")
        lines.append("")
        lines.append("**感情・気づき:**")
        lines.append("")
        lines.append("**来週に持ち越すテーマ:**")
        lines.append("")
    
    return "\n".join(lines)


# ============================================================
# 6. DAILY DBにページを作成
# ============================================================
def create_daily_page(today: datetime.date, content: str) -> str:
    """DAILY DBに当日のデイリーノートページを作成してIDを返す"""
    import glob
    
    weekdays_ja = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = weekdays_ja[today.weekday()]
    title = f"{today.strftime('%Y-%m-%d')}（{weekday}）"
    is_friday = today.weekday() == 4
    page_type = "Weekly Review" if is_friday else "Daily"
    
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-create-pages",
        "--server", "notion",
        "--input", json.dumps({
            "parent": {"data_source_id": DAILY_DS_ID},
            "pages": [{
                "properties": {
                    "DATE": title,
                    "date:DATE_RAW:start": today.isoformat(),
                    "date:DATE_RAW:is_datetime": 0,
                    "TYPE": page_type
                },
                "icon": "📅",
                "content": content
            }]
        }, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    
    files = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-create-pages*.json"), reverse=True)
    if not files:
        return ""
    
    try:
        with open(files[0]) as f:
            data = json.load(f)
        pages = data.get("pages", [])
        if pages:
            page_id = pages[0].get("id", "")
            page_url = pages[0].get("url", "")
            print(f"[OK] デイリーノート作成: {title}")
            print(f"     URL: {page_url}")
            return page_id
    except Exception as e:
        print(f"[ERROR] デイリーノート作成失敗: {e}")
    return ""


# ============================================================
# 7. MIGRATEDフラグの更新
# ============================================================
def mark_tasks_migrated(tasks: list[dict]):
    """取得したタスクのMIGRATEDをtrueに更新"""
    for task in tasks:
        if not task.get("url"):
            continue
        cmd = [
            "manus-mcp-cli", "tool", "call", "notion-update-page",
            "--server", "notion",
            "--input", json.dumps({
                "page_url": task["url"],
                "properties": {"MIGRATED": "__YES__"}
            }, ensure_ascii=False)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if "error" not in result.stdout.lower():
            print(f"[OK] MIGRATED更新: {task['name']}")


# ============================================================
# メイン処理
# ============================================================
def scan_previous_daily_and_sync(yesterday: datetime.date):
    """
    前日のデイリーノートのTODAYセクションをスキャンし、TASK DBに同期する
    - チェック済み → STATUS=DONEでTASK DBに登録
    - 未チェック → STATUS=TODO, MIGRATED=falseでTASK DBに登録
    """
    import glob
    weekdays_ja = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = weekdays_ja[yesterday.weekday()]
    title = f"{yesterday.strftime('%Y-%m-%d')}（{weekday}）"

    print(f"[0/6] 前日（{title}）のデイリーをスキャン中...")

    # 前日のページを検索
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-search",
        "--server", "notion",
        "--input", json.dumps({"query": title}, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True)

    files = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-search*.json"), reverse=True)
    if not files:
        print("      → 前日のデイリーが見つかりませんでした（スキップ）")
        return

    try:
        with open(files[0]) as f:
            data = json.load(f)
        results = data.get("results", [])
        page_url = ""
        for r in results:
            if title in r.get("title", ""):
                page_url = r.get("url", "")
                break
        if not page_url:
            print("      → 前日のデイリーページが見つかりませんでした（スキップ）")
            return
    except Exception as e:
        print(f"      → エラー: {e}")
        return

    # ページ本文を取得
    cmd2 = [
        "manus-mcp-cli", "tool", "call", "notion-fetch",
        "--server", "notion",
        "--input", json.dumps({"url": page_url}, ensure_ascii=False)
    ]
    subprocess.run(cmd2, capture_output=True, text=True)

    files2 = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-fetch*.json"), reverse=True)
    if not files2:
        return

    try:
        with open(files2[0]) as f:
            data2 = json.load(f)
        content = data2.get("result", "")
    except Exception:
        return

    # TODAYセクションを抽出してチェックボックスをパース
    in_today = False
    completed, pending = [], []
    for line in content.split("\n"):
        if "✅ TODAY" in line or "## TODAY" in line:
            in_today = True
            continue
        if in_today and line.startswith("## "):
            break
        if in_today:
            # チェック済み: [x] or [✓]
            m_done = re.search(r'\[x\]\s*(.+)', line, re.IGNORECASE)
            m_todo = re.search(r'\[ \]\s*(.+)', line)
            if m_done:
                name = re.sub(r'\[.*?\]\(.*?\)', lambda m: m.group(0).split('](')[0][1:], m_done.group(1)).strip()
                name = re.sub(r'^[U0001f501\[PJ:.*?\]]\s*', '', name).strip()
                if name:
                    completed.append(name)
            elif m_todo:
                name = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', m_todo.group(1)).strip()
                name = re.sub(r'^[U0001f501]\s*', '', name).strip()
                name = re.sub(r'^\[PJ:[^\]]+\]\s*', '', name).strip()
                if name and name != "（タスクを追加）":
                    pending.append(name)

    print(f"      → 完了: {len(completed)}件, 未完了: {len(pending)}件")

    # TASK DBに登録
    def register_task(name: str, status: str):
        cmd = [
            "manus-mcp-cli", "tool", "call", "notion-create-pages",
            "--server", "notion",
            "--input", json.dumps({
                "parent": {"data_source_id": TASK_DS_ID},
                "pages": [{
                    "properties": {
                        "TASK": name,
                        "STATUS": status,
                        "MIGRATED": "__NO__" if status == "TODO" else "__YES__"
                    }
                }]
            }, ensure_ascii=False)
        ]
        subprocess.run(cmd, capture_output=True, text=True)

    for name in completed:
        register_task(name, "DONE")
        print(f"      [DONE] {name}")
    for name in pending:
        register_task(name, "TODO")
        print(f"      [TODO] {name}")


def main():
    now = datetime.datetime.now(JST)
    today = now.date()
    yesterday = today - datetime.timedelta(days=1)

    print(f"=== LIFE OS デイリーノート生成 {today} ===")

    # 0. 前日スキャン → TASK DB同期
    scan_previous_daily_and_sync(yesterday)

    # 1. 未完了タスク取得
    print("[1/6] 未完了タスクを取得中...")
    pending_tasks = get_pending_tasks_v2()
    print(f"      → {len(pending_tasks)}件の引き継ぎタスク")
    
    # 2. プロジェクト情報取得
    print("[2/6] プロジェクト情報を取得中...")
    projects = get_active_projects()
    print(f"      → {len(projects)}件のACTIVEプロジェクト")
    
    # 3. カレンダーイベント取得
    print("[3/6] Googleカレンダーを取得中...")
    events = get_today_events(today)
    print(f"      → {len(events)}件のイベント")
    
    # 4. デイリーノートページを先に作成（ミーティングサブページの親として必要）
    print("[4/6] デイリーノートを作成中...")
    
    # まず空のコンテンツで作成してIDを取得
    temp_content = "（生成中...）"
    daily_page_id = create_daily_page(today, temp_content)
    
    if not daily_page_id:
        print("[ERROR] デイリーノートの作成に失敗しました")
        sys.exit(1)
    
    # 5. ミーティングサブページ作成
    meeting_links = {}
    meeting_events = [ev for ev in events if ev.get("is_meeting")]
    if meeting_events:
        print(f"[5/6] ミーティングサブページを作成中（{len(meeting_events)}件）...")
        for ev in meeting_events:
            page_url = create_meeting_page(daily_page_id, ev, today)
            if page_url:
                meeting_links[ev["summary"]] = page_url
                print(f"      → {ev['summary']}: {page_url}")
    else:
        print("[5/6] ミーティングなし（サブページ作成スキップ）")
    
    # 6. 本コンテンツを生成してページを更新
    print("[6/6] コンテンツを更新中...")
    content = build_daily_content(today, pending_tasks, projects, events, meeting_links)
    
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-update-page",
        "--server", "notion",
        "--input", json.dumps({
            "page_id": daily_page_id,
            "content": content
        }, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    
    # 7. MIGRATEDフラグ更新
    if pending_tasks:
        print("[7/7] MIGRATEDフラグを更新中...")
        mark_tasks_migrated(pending_tasks)
    
    print(f"\n✅ 完了: デイリーノート {today} を生成しました")


if __name__ == "__main__":
    main()
