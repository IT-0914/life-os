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
import os
import glob

# タグ処理モジュール（同ディレクトリに配置）
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from tag_processor import process_tags_in_daily
    TAG_PROCESSOR_AVAILABLE = True
except ImportError as e:
    print(f"[WARN] tag_processor をインポートできません: {e}")
    TAG_PROCESSOR_AVAILABLE = False

# ============================================================
# 設定
# ============================================================
DAILY_DS_ID   = "f9b89321-f903-4167-b022-0787096ea6f3"
TASK_DS_ID    = "47ae00b4-956c-4fa4-a786-af39a5b33067"
PROJ_DS_ID    = "eadd59d6-f0d5-4356-bc13-76aeab13c0ec"
DAILY_DB_ID   = "77de58c499d14be9817ebd539c551eb0"
DAILY_DB_URL  = "https://www.notion.so/77de58c499d14be9817ebd539c551eb0"
TASK_DB_URL   = "https://www.notion.so/6135d9e113d64fba81c4d12d3ac24bfe"
PROJ_DB_URL   = "https://www.notion.so/ae6d2424256c47249c5cdccf644560bc"
DB_HUB_URL    = "https://www.notion.so/370200b3cc70817d9fcad1c4190f79fe"
HOME_PAGE_ID  = "370200b3cc7081a6ba0debd25cdf34d2"  # 🏠 TODAY HOME
HOME_PAGE_URL = "https://www.notion.so/370200b3cc7081a6ba0debd25cdf34d2"

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


def mcp_call_file(tool: str, server: str, input_dict: dict) -> dict:
    """
    MCP CLIを呼び出し、結果ファイルを返す。
    MANUS_MCP_RESULT_PATHを新規パスに上書きすることで常に新しい結果を取得する。
    """
    import uuid
    # 新規パスを指定して必ず新ファイルに書き込ませる
    tmp_dir = "/tmp/manus-mcp/"
    os.makedirs(tmp_dir, exist_ok=True)
    result_path = f"{tmp_dir}mcp_result_{uuid.uuid4()}.json"
    
    env = os.environ.copy()
    env["MANUS_MCP_RESULT_PATH"] = result_path
    env["MANUS_MCP_RESULT_FILEPATH"] = result_path
    
    cmd = [
        "manus-mcp-cli", "tool", "call", tool,
        "--server", server,
        "--input", json.dumps(input_dict, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True, env=env)
    
    # 指定パスのファイルを読む
    if os.path.exists(result_path):
        try:
            with open(result_path) as f:
                return json.load(f)
        except Exception:
            pass
    
    # フォールバック: notion系の最新ファイルを返す
    notion_dir = "/home/ubuntu/.mcp/tool-results/"
    all_files = sorted(
        list(glob.glob(f"{notion_dir}*.json")) + list(glob.glob(f"{tmp_dir}mcp_result_*.json")),
        reverse=True
    )
    if all_files:
        try:
            with open(all_files[0]) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


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
    data = mcp_call_file("notion-fetch", "notion", {"id": f"collection://{TASK_DS_ID}"})
    
    try:
        
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
    data = mcp_call_file("notion-fetch", "notion", {"id": f"collection://{PROJ_DS_ID}"})
    
    try:
        
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
    """
    当日のGoogleカレンダーイベントを取得。
    playbookの STEP 2 で之めエージェントが /tmp/today_calendar.json に保存したファイルを読む。
    """
    calendar_file = "/tmp/today_calendar.json"
    try:
        if not os.path.exists(calendar_file):
            print("      [INFO] /tmp/today_calendar.json がないためカレンダーはスキップ")
            return []
        with open(calendar_file) as f:
            items = json.load(f)
        if not isinstance(items, list):
            return []
        events = []
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
        print(f"      → カレンダー: {len(events)}件取得")
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

    # ── SORA TAG HINT ──
    lines.append("> 💡 **SORAタグ** — このページのどこにでも書くだけで翌朝7:00に自動実行されます")
    lines.append("> - `<調べたいこと> #調査` → Web調査して翌日デイリーに結果を追記")
    lines.append("> - `<送りたい内容> #メール` → Gmail下書きを自動作成")
    lines.append("> - `<やること> #タスク` → TASK DBに自動登録")
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
def scan_previous_daily_and_sync(yesterday: datetime.date, page_text: str = ""):
    """
    前日のデイリーノートのTODAYセクションをスキャンし、TASK DBに同期する。
    page_text: fetch_yesterday_page()で取得済みのページ本文（省略時は再取得）
    """
    import glob
    weekdays_ja = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = weekdays_ja[yesterday.weekday()]
    title = f"{yesterday.strftime('%Y-%m-%d')}（{weekday}）"

    print(f"[0a/6] 前日（{title}）のTODAYセクションをTASK DBに同期中...")

    content = page_text

    if not content:
        # 前日ページ本文が渡されていない場合は再取得
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


def get_week_events(today: datetime.date) -> dict:
    """今日から翻日までの7日間のGoogleカレンダーイベントを取得。日付をキーにイベントリストを返す"""
    week_end = today + datetime.timedelta(days=6)
    time_min = f"{today.isoformat()}T00:00:00+09:00"
    time_max = f"{week_end.isoformat()}T23:59:59+09:00"
    
    data = mcp_call_file("google_calendar_search_events", "google-calendar", {
        "time_min": time_min,
        "time_max": time_max,
        "max_results": 50,
        "calendar_id": "primary"
    })
    
    # Google Calendarの結果が「unfinished tool call」の場合はスキップ
    if data.get("message") == "This is an unfinished tool call. The actual API execution will be handled by the server.":
        print("      [INFO] カレンダー：エージェント実行時に取得されます")
        return {}
    
    try:
        week_events = {}
        # Google Calendarのresultキーは配列形式
        items = data.get("result", data.get("events", data.get("items", [])))
        if isinstance(items, list):
            for item in items:
                start = item.get("start", {})
                start_time = start.get("dateTime", start.get("date", ""))
                summary = item.get("summary", "（タイトルなし）")
                
                # 日付を取得
                if "T" in start_time:
                    dt = datetime.datetime.fromisoformat(start_time)
                    date_key = dt.date().isoformat()
                    time_str = dt.strftime("%H:%M")
                else:
                    date_key = start_time[:10] if start_time else ""
                    time_str = "終日"
                
                if date_key:
                    if date_key not in week_events:
                        week_events[date_key] = []
                    week_events[date_key].append({"time": time_str, "summary": summary})
        
        # 各日のイベントを時刻順にソート
        for date_key in week_events:
            week_events[date_key].sort(key=lambda x: x["time"] if x["time"] != "終日" else "00:00")
        
        return week_events
    except Exception as e:
        print(f"[WARN] 週間カレンダー取得エラー: {e}")
        return {}


def get_all_todo_tasks() -> list[dict]:
    """
    TASK DBの未完了タスク（STATUS=TODO/DOING）を全件取得。
    notion-searchでTASK DB配下のページを取得し、各ページのpropertiesからSTATUSを確認する。
    """
    # STEP1: TASK DB配下の全ページを検索（data_source_urlでTASK DB配下に絞り込む）
    search_data = mcp_call_file("notion-search", "notion", {
        "query": " ",
        "data_source_url": f"collection://{TASK_DS_ID}",
        "page_size": 25
    })
    
    try:
        tasks = []
        results = search_data.get("results", [])
        
        # TASK DB配下のページのみをフィルタリング
        task_db_url = TASK_DB_URL.rstrip("/")
        task_pages = []
        for r in results:
            page_url = r.get("url", "")
            page_id = r.get("id", "")
            # ページをfetchしてancestor-pathでTASK DB配下かどうか確認
            task_pages.append({"id": page_id, "title": r.get("title", ""), "url": page_url})
        
        if not task_pages:
            return []
        
        # STEP2: 各ページをfetchしてSTATUSを確認（最大20件まで）
        for page_info in task_pages[:20]:
            page_data = mcp_call_file("notion-fetch", "notion", {"id": page_info["id"]})
            page_text = page_data.get("text", page_data.get("result", ""))
            
            # TASK DB配下のページか確認
            if f"collection://{TASK_DS_ID}" not in page_text:
                continue
            
            # properties JSONを抽出
            props_match = re.search(r'<properties>\s*(\{.*?\})\s*</properties>', page_text, re.DOTALL)
            if not props_match:
                continue
            
            try:
                props = json.loads(props_match.group(1))
                status = props.get("STATUS", "")
                task_name = props.get("TASK", page_info["title"])
                task_url = props.get("url", page_info["url"])
                
                if status in ("TODO", "DOING") and task_name:
                    tasks.append({"name": task_name, "url": task_url, "status": status})
            except json.JSONDecodeError:
                continue
        
        return tasks
    except Exception as e:
        print(f"[WARN] タスク取得エラー: {e}")
        return []


def update_home_page(today: datetime.date, daily_page_id: str):
    """
    🏠 TODAY HOMEページを完全再構築する。
    内容：当日デイリーリンク・1週間カレンダー・プロジェクト一覧・未完了タスク一覧
    """
    print("[8/8] TODAY HOMEを再構築中...")
    weekdays_ja = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = weekdays_ja[today.weekday()]
    title = f"{today.strftime('%Y-%m-%d')}（{weekday}）"
    daily_url = f"https://www.notion.so/{daily_page_id.replace('-', '')}"

    # --- 1週間カレンダーを取得 ---
    print("      → 1週間カレンダーを取得中...")
    week_events = get_week_events(today)

    # --- 未完了タスクを取得 ---
    print("      → 未完了タスクを取得中...")
    all_tasks = get_all_todo_tasks()

    # --- プロジェクトを取得（既存の関数を再利用） ---
    print("      → プロジェクトを取得中...")
    projects = get_active_projects()

    # --- 1週間カレンダーセクションを構築 ---
    cal_lines = ["## 📆 週間カレンダー"]
    for i in range(7):
        day = today + datetime.timedelta(days=i)
        day_str = day.isoformat()
        wd = weekdays_ja[day.weekday()]
        day_label = f"{day.strftime('%m/%d')}({wd})"
        is_today = (i == 0)
        today_mark = " ◄ TODAY" if is_today else ""

        day_events = week_events.get(day_str, [])
        if day_events:
            cal_lines.append(f"- **{day_label}{today_mark}**")
            for ev in day_events:
                cal_lines.append(f"  - {ev['time']} {ev['summary']}")
        else:
            cal_lines.append(f"- {day_label}　予定なし{today_mark}")
    cal_section = "\n".join(cal_lines) + "\n"

    # --- プロジェクトセクションを構築 ---
    proj_lines = ["## 🚀 プロジェクト\n"]
    if projects:
        for pj in projects:
            pj_name = pj['name']
            pj_url = pj.get('url', '')
            pj_progress = pj.get('progress', '0%')
            pj_next = pj.get('next_action', '（未設定）')
            if pj_url:
                proj_lines.append(f"- [{pj_name}]({pj_url}) {pj_progress}")
            else:
                proj_lines.append(f"- {pj_name} {pj_progress}")
            if pj_next and pj_next != '（未設定）':
                proj_lines.append(f"  - → {pj_next}")
    else:
        proj_lines.append("アクティブなプロジェクトなし")
    proj_section = "\n".join(proj_lines) + "\n"

    # --- 未完了タスクセクションを構築 ---
    task_lines = ["## ✅ 未完了タスク\n"]
    if all_tasks:
        doing_tasks = [t for t in all_tasks if t.get('status') == 'DOING']
        todo_tasks = [t for t in all_tasks if t.get('status') == 'TODO']
        if doing_tasks:
            task_lines.append("**進行中 (DOING)**")
            for t in doing_tasks:
                if t.get('url'):
                    task_lines.append(f"- [{t['name']}]({t['url']})")
                else:
                    task_lines.append(f"- {t['name']}")
        if todo_tasks:
            if doing_tasks:
                task_lines.append("")
            task_lines.append("**未着手 (TODO)**")
            for t in todo_tasks:
                if t.get('url'):
                    task_lines.append(f"- [{t['name']}]({t['url']})")
                else:
                    task_lines.append(f"- {t['name']}")
    else:
        task_lines.append("未完了タスクなし 🎉")
    task_section = "\n".join(task_lines) + "\n"

    # --- ページ全体を構築 ---
    new_content = (
        f"# 🏠 TODAY HOME\n\n"
        f"> 毎朝7:00に自動更新。当日デイリー・週間カレンダー・プロジェクト・未完了タスクを一元管理。\n\n"
        f"---\n\n"
        f"## 📅 今日のデイリー\n\n"
        f"[→ {title}のデイリーを開く]({daily_url})\n\n"
        f"---\n\n"
        f"{cal_section}\n"
        f"---\n\n"
        f"{proj_section}\n"
        f"---\n\n"
        f"{task_section}\n"
        f"---\n\n"
        f"## 💡 SORAタグの使い方\n\n"
        f"| タグ | 書き方の例 | 実行されること |\n"
        f"|---|---|---|\n"
        f"| `#調査` | `競合A社の最新動向を調べて #調査` | Web調査して翌日デイリーにSORA REPORTとして追記 |\n"
        f"| `#メール` | `田中部長に打ち合わせ調整 #メール` | Gmail下書きを作成（宛先は手動で設定） |\n"
        f"| `#タスク` | `LPのワイヤーフレームを作る #タスク` | TASK DBにTODOで自動登録 |\n\n"
        f"**ルール:** `<指示内容> #タグ名` の順（タグは末尾）・1行に1タグ\n\n"
        f"---\n\n"
        f"## 🗂 ナビゲーション\n\n"
        f"- [📅 DAILY一覧]({DAILY_DB_URL})\n"
        f"- [✅ TASK]({TASK_DB_URL})\n"
        f"- [🚀 PROJECT]({PROJ_DB_URL})\n"
        f"- [🧠 LIFE OS TOP](https://www.notion.so/370200b3cc708115a943d66ec4ed1206)\n"
    )

    # --- Notionページを完全上書き (replace_contentコマンドを使用) ---
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-update-page",
        "--server", "notion",
        "--input", json.dumps({
            "page_id": HOME_PAGE_ID,
            "command": "replace_content",
            "new_str": new_content
        }, ensure_ascii=False)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"      → TODAY HOME更新完了: {title}")
        print(f"      → カレンダー: {len(week_events)}日分 / プロジェクト: {len(projects)}件 / 未完了タスク: {len(all_tasks)}件")
        print(f"      → {HOME_PAGE_URL}")
    else:
        print(f"      [WARN] TODAY HOME更新失敗: {result.stderr[:200]}")
        print(f"      → {HOME_PAGE_URL}")


def scan_previous_daily_tags(
    yesterday: datetime.date,
    today_page_id: str = "",
    yesterday_page_id: str = "",
    yesterday_page_text: str = ""
):
    """
    前日のデイリーノートのタグ（#調査・#メール・#タスク）をスキャンして自動実行する。
    yesterday_page_id/text: fetch_yesterday_page()で取得済みの場合は再取得をスキップ。
    """
    if not TAG_PROCESSOR_AVAILABLE:
        print("      → tag_processor 未利用可能（スキップ）")
        return

    weekdays_ja = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = weekdays_ja[yesterday.weekday()]
    title = f"{yesterday.strftime('%Y-%m-%d')}（{weekday}）"
    print(f"[0b/6] 前日（{title}）のタグをスキャン中...")

    # 当日ページIDがない場合はスキップ
    if not today_page_id:
        print("      → 当日ページIDが未設定（スキップ）")
        return

    # 取得済みの前日ページ情報を利用（クレジット削減）
    page_id = yesterday_page_id
    page_text = yesterday_page_text

    if not page_id or not page_text:
        # 引数で渡されていない場合は再取得
        cmd = [
            "manus-mcp-cli", "tool", "call", "notion-search",
            "--server", "notion",
            "--input", json.dumps({"query": title}, ensure_ascii=False)
        ]
        subprocess.run(cmd, capture_output=True, text=True)
        files = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-search*.json"), reverse=True)
        if not files:
            print("      → 前日ページが見つかりませんでした（スキップ）")
            return
        try:
            with open(files[0]) as f:
                data = json.load(f)
            results = data.get("results", [])
            page_url = ""
            for r in results:
                if title in r.get("title", ""):
                    page_id = r.get("id", "")
                    page_url = r.get("url", "")
                    break
            if not page_id:
                print("      → 前日ページIDが取得できませんでした（スキップ）")
                return
        except Exception as e:
            print(f"      → エラー: {e}")
            return
        cmd2 = [
            "manus-mcp-cli", "tool", "call", "notion-fetch",
            "--server", "notion",
            "--input", json.dumps({"url": page_url}, ensure_ascii=False)
        ]
        subprocess.run(cmd2, capture_output=True, text=True)
        files2 = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-fetch*.json"), reverse=True)
        if not files2:
            print("      → ページ本文の取得に失敗しました（スキップ）")
            return
        try:
            with open(files2[0]) as f:
                data2 = json.load(f)
            page_text = data2.get("result", "")
        except Exception as e:
            print(f"      → ページ本文パースエラー: {e}")
            return
    else:
        print("      → 取得済みページ情報を流用（再取得スキップ）")

    # タグ処理を実行（結果は当日ページに追記）
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    processed = process_tags_in_daily(
        yesterday_page_id=page_id,
        yesterday_page_text=page_text,
        today_page_id=today_page_id,
        task_ds_id=TASK_DS_ID,
        proj_ds_id=PROJ_DS_ID,
        yesterday_str=yesterday_str
    )
    if processed:
        print(f"      → {len(processed)}件のタグを処理しました")
        for item in processed:
            status_icon = "✅" if item.get("status") == "success" else "❌"
            print(f"         {status_icon} {item['tag']} : {item['instruction'][:30]}")
    else:
        print("      → タグなし（スキップ）")


def fetch_yesterday_page(yesterday: datetime.date) -> tuple[str, str, str]:
    """
    前日のデイリーページを検索・取得して返す。
    戻り値: (page_id, page_url, page_text)
    前日ページが見つからない場合は ('', '', '') を返す。
    """
    weekdays_ja = ['月', '火', '水', '木', '金', '土', '日']
    weekday = weekdays_ja[yesterday.weekday()]
    title = f"{yesterday.strftime('%Y-%m-%d')}（{weekday}）"

    # 検索
    cmd = [
        "manus-mcp-cli", "tool", "call", "notion-search",
        "--server", "notion",
        "--input", json.dumps({"query": title}, ensure_ascii=False)
    ]
    subprocess.run(cmd, capture_output=True, text=True)

    files = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-search*.json"), reverse=True)
    if not files:
        return ('', '', '')

    try:
        with open(files[0]) as f:
            data = json.load(f)
        results = data.get("results", [])
        page_id, page_url = '', ''
        for r in results:
            if title in r.get("title", ""):
                page_id = r.get("id", "")
                page_url = r.get("url", "")
                break
        if not page_url:
            return ('', '', '')
    except Exception:
        return ('', '', '')

    # 本文取得
    cmd2 = [
        "manus-mcp-cli", "tool", "call", "notion-fetch",
        "--server", "notion",
        "--input", json.dumps({"url": page_url}, ensure_ascii=False)
    ]
    subprocess.run(cmd2, capture_output=True, text=True)

    files2 = sorted(glob.glob("/home/ubuntu/.mcp/tool-results/*notion-fetch*.json"), reverse=True)
    if not files2:
        return (page_id, page_url, '')

    try:
        with open(files2[0]) as f:
            data2 = json.load(f)
        page_text = data2.get("result", data2.get("text", ""))
        return (page_id, page_url, page_text)
    except Exception:
        return (page_id, page_url, '')


def main():
    now = datetime.datetime.now(JST)
    today = now.date()
    yesterday = today - datetime.timedelta(days=1)

    print(f"=== LIFE OS デイリーノート生成 {today} ===")

    # 0. 前日ページを一度だけ取得（クレジット削減：後続の検索・fetchを共有）
    print(f"[0/6] 前日（{yesterday}）のページを取得中...")
    yesterday_page_id, yesterday_page_url, yesterday_page_text = fetch_yesterday_page(yesterday)
    if yesterday_page_url:
        print(f"      → 前日ページ取得完了: {yesterday_page_url[:60]}")
    else:
        print("      → 前日ページが見つかりませんでした（スキップ）")

    # 0a. TASK DB同期（取得済みページを利用）
    scan_previous_daily_and_sync(yesterday, yesterday_page_text)

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
            "command": "replace_content",
            "new_str": content
        }, ensure_ascii=False)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print("      → コンテンツ更新完了")
    else:
        print(f"      [WARN] コンテンツ更新失敗: {result.stderr[:200]}")
    
    # 7. MIGRATEDフラグ更新
    if pending_tasks:
        print("[7/7] MIGRATEDフラグを更新中...")
        mark_tasks_migrated(pending_tasks)

    # 0b. 前日デイリーのタグ自動実行（当日ページに結果を追記）
    # 取得済みの前日ページ情報を流用して再取得をスキップ
    scan_previous_daily_tags(
        yesterday,
        today_page_id=daily_page_id,
        yesterday_page_id=yesterday_page_id,
        yesterday_page_text=yesterday_page_text
    )

    # TODAY HOME更新（週間カレンダー・プロジェクト・未完了タスクを一元表示）
    update_home_page(today, daily_page_id)

    print(f"\n✅ 完了: デイリーノート {today} を生成しました")


if __name__ == "__main__":
    main()
