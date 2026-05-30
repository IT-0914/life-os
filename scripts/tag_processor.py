"""
tag_processor.py
================
Notionデイリーノートのタグ自動実行モジュール。
前日デイリーページ全体のテキストをスキャンし、未処理タグを検出して実行する。
実行結果は「当日（翌日）のデイリーページ」に出力する。

対応タグ:
  #調査  → Web調査して当日デイリーのSORA REPORTセクションに追記
  #メール → Gmail下書きを作成して当日デイリーに記録
  #タスク → TASK DBに自動登録して当日デイリーに記録

タグ書式:
  <指示内容> #タグ名
  例: 「競合A社の最新動向を調べて #調査」
  例: 「田中部長にプロジェクト進捗報告をメール #メール」
  例: 「ランディングページのワイヤーフレームを作る #タスク」

出力先:
  当日（翌日）のデイリーページに「## 🤖 SORA REPORT」セクションとして追記する。
  前日ページは変更しない。
"""

import json
import re
import subprocess
import datetime
import os

JST = datetime.timezone(datetime.timedelta(hours=9))

# ============================================================
# MCP呼び出しユーティリティ
# ============================================================

def mcp_call(tool: str, server: str, input_dict: dict) -> dict:
    """manus-mcp-cli tool call のラッパー"""
    cmd = [
        "manus-mcp-cli", "tool", "call", tool,
        "--server", server,
        "--input", json.dumps(input_dict, ensure_ascii=False)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"MCP error [{tool}]: {result.stderr[:300]}")
    # 結果ファイルパスを取得
    for line in result.stdout.splitlines():
        if "result_file_path" in line:
            path = line.split("result_file_path")[-1].strip().strip('"').strip("'").strip()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
    # フォールバック: stdoutをJSONとして解析
    try:
        return json.loads(result.stdout)
    except Exception:
        return {"raw": result.stdout}


def search_web(query: str) -> str:
    """Web検索を実行してテキスト結果を返す（requests + DuckDuckGo）"""
    try:
        import requests
        url = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        abstract = data.get("AbstractText", "")
        related = [r.get("Text", "") for r in data.get("RelatedTopics", [])[:3] if isinstance(r, dict)]
        if abstract:
            return abstract + "\n\n関連情報:\n" + "\n".join(f"- {r}" for r in related if r)
        elif related:
            return "関連情報:\n" + "\n".join(f"- {r}" for r in related if r)
        else:
            return f"「{query}」の検索結果が見つかりませんでした。"
    except Exception as e:
        return f"検索エラー: {str(e)}"


# ============================================================
# タグ検出
# ============================================================

TAG_PATTERN = re.compile(
    r'(.+?)\s+(#調査|#メール|#タスク)(?!\s*処理済み)',
    re.MULTILINE
)

def extract_tags(text: str) -> list[dict]:
    """
    テキストからタグ付き指示を抽出する。
    Returns: [{"instruction": str, "tag": str, "original_line": str}, ...]
    """
    results = []
    for match in TAG_PATTERN.finditer(text):
        instruction = match.group(1).strip()
        tag = match.group(2).strip()
        original_line = match.group(0)
        # チェックボックス記法を除去
        instruction = re.sub(r'^[-*]\s*\[[ x]\]\s*', '', instruction)
        instruction = re.sub(r'^[-*]\s*', '', instruction)
        # 空行・見出し記号を除去
        instruction = instruction.lstrip('#').strip()
        if instruction:
            results.append({
                "instruction": instruction,
                "tag": tag,
                "original_line": original_line
            })
    return results


# ============================================================
# タグ別処理（結果テキストを返す）
# ============================================================

def process_調査(instruction: str, yesterday_str: str) -> str:
    """#調査: Web調査して結果テキストを返す"""
    print(f"  [#調査] 調査中: {instruction}")
    result = search_web(instruction)
    return (
        f"### 🔍 #調査 — {instruction}\n"
        f"> 元ページ: {yesterday_str}\n>\n"
        + "\n".join(f"> {line}" for line in result.splitlines())
        + "\n"
    )


def process_メール(instruction: str, yesterday_str: str) -> str:
    """#メール: Gmail下書きを作成して結果テキストを返す"""
    print(f"  [#メール] 下書き作成中: {instruction}")
    today = datetime.datetime.now(JST).strftime("%Y-%m-%d")

    subject = f"【ご連絡】{instruction[:30]}"
    body = (
        f"お疲れ様です。\n\n"
        f"{instruction}\n\n"
        f"以上、よろしくお願いいたします。\n"
    )

    try:
        result = mcp_call(
            "gmail_create_draft",
            "gmail",
            {
                "to": "",
                "subject": subject,
                "body": body
            }
        )
        draft_id = result.get("id", "（ID取得失敗）")
        return (
            f"### 📧 #メール — {instruction}\n"
            f"> 元ページ: {yesterday_str}\n"
            f"> 件名: {subject}\n"
            f"> Gmail下書きID: {draft_id}\n"
            f"> → Gmailを開いて宛先・本文を確認・編集してください\n"
        )
    except Exception as e:
        return (
            f"### 📧 #メール — {instruction}\n"
            f"> 元ページ: {yesterday_str}\n"
            f"> ❌ 下書き作成失敗: {str(e)[:100]}\n"
            f"> → 手動でGmailから作成してください\n"
        )


def process_タスク(instruction: str, task_ds_id: str, proj_ds_id: str, yesterday_str: str) -> str:
    """#タスク: TASK DBに登録して結果テキストを返す"""
    print(f"  [#タスク] TASK DB登録中: {instruction}")

    try:
        result = mcp_call(
            "notion-create-page",
            "notion",
            {
                "parent_id": task_ds_id,
                "properties": {
                    "TASK": {"title": [{"text": {"content": instruction}}]},
                    "STATUS": {"select": {"name": "TODO"}},
                    "MIGRATED": {"checkbox": False}
                }
            }
        )
        page_url = result.get("url", "")
        task_link = f"[{instruction}]({page_url})" if page_url else instruction
        return (
            f"### ✅ #タスク — {task_link}\n"
            f"> 元ページ: {yesterday_str}\n"
            f"> TASK DBに登録しました\n"
        )
    except Exception as e:
        return (
            f"### ✅ #タスク — {instruction}\n"
            f"> 元ページ: {yesterday_str}\n"
            f"> ❌ 登録失敗: {str(e)[:100]}\n"
            f"> → 手動でTASK DBに登録してください\n"
        )


# ============================================================
# メイン処理
# ============================================================

def process_tags_in_daily(
    yesterday_page_id: str,
    yesterday_page_text: str,
    today_page_id: str,
    task_ds_id: str,
    proj_ds_id: str,
    yesterday_str: str = ""
) -> list[dict]:
    """
    前日デイリーページのテキストからタグを検出して処理し、
    結果を当日（翌日）のデイリーページに「## 🤖 SORA REPORT」セクションとして追記する。

    Args:
        yesterday_page_id: 前日デイリーページID（参照のみ・変更しない）
        yesterday_page_text: 前日デイリーページのテキスト
        today_page_id: 当日デイリーページID（結果の出力先）
        task_ds_id: TASK DB データソースID
        proj_ds_id: PROJECT DB データソースID
        yesterday_str: 前日の日付文字列（表示用）

    Returns: 処理したタグのリスト
    """
    tags = extract_tags(yesterday_page_text)
    if not tags:
        print("  タグなし。スキップ。")
        return []

    print(f"  {len(tags)}件のタグを検出: {[t['tag'] for t in tags]}")
    processed = []
    report_sections = []

    for item in tags:
        instruction = item["instruction"]
        tag = item["tag"]
        section_text = ""

        if tag == "#調査":
            section_text = process_調査(instruction, yesterday_str)
        elif tag == "#メール":
            section_text = process_メール(instruction, yesterday_str)
        elif tag == "#タスク":
            section_text = process_タスク(instruction, task_ds_id, proj_ds_id, yesterday_str)

        if section_text:
            report_sections.append(section_text)
            processed.append({"tag": tag, "instruction": instruction, "status": "success"})

    if not report_sections:
        return processed

    # 当日デイリーページに「## 🤖 SORA REPORT」セクションとして一括追記
    today = datetime.datetime.now(JST).strftime("%Y-%m-%d")
    report_content = (
        f"\n---\n\n"
        f"## 🤖 SORA REPORT ({yesterday_str} のタグ処理結果)\n\n"
        + "\n".join(report_sections)
    )

    try:
        mcp_call(
            "notion-update-page",
            "notion",
            {
                "id": today_page_id,
                "content": report_content,
                "command": "append_content"
            }
        )
        print(f"  ✅ 当日デイリーページ（{today_page_id[:8]}...）にSORA REPORTを追記しました")
    except Exception as e:
        print(f"  ❌ 当日デイリーページへの追記失敗: {e}")
        for item in processed:
            item["status"] = "error"
            item["error"] = str(e)

    return processed


if __name__ == "__main__":
    # テスト用
    test_text = """
## ✅ TODAY
- [ ] 通常タスク
- [ ] 競合A社の最新AI動向を調べて #調査
- [ ] 田中部長に来週の打ち合わせ日程調整メール #メール

## 📝 LOG
ランディングページのワイヤーフレームを作る #タスク
    """
    tags = extract_tags(test_text)
    print(f"検出タグ: {tags}")
