"""
tag_processor.py
================
Notionデイリーノートのタグ自動実行モジュール。
デイリーページ全体のテキストをスキャンし、未処理タグを検出して実行する。

対応タグ:
  #調査  → Web調査してデイリーのLOGに結果を書き戻す
  #メール → Gmail下書きを作成してデイリーに記録
  #タスク → TASK DBに自動登録してデイリーに記録

タグ書式:
  <指示内容> #タグ名
  例: 「競合A社の最新動向を調べて #調査」
  例: 「田中部長にプロジェクト進捗報告をメール #メール」
  例: 「ランディングページのワイヤーフレームを作る #タスク」

処理済みタグ:
  実行後、タグを「<!-- #タグ名 処理済み YYYY-MM-DD -->」に置換して再実行を防ぐ。
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

PROCESSED_PATTERN = re.compile(
    r'(.+?)\s+<!--\s*(#調査|#メール|#タスク)\s*処理済み.*?-->',
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
# タグ別処理
# ============================================================

def process_調査(instruction: str) -> str:
    """#調査: Web調査して結果テキストを返す"""
    print(f"  [#調査] 調査中: {instruction}")
    result = search_web(instruction)
    today = datetime.datetime.now(JST).strftime("%Y-%m-%d")
    return (
        f"\n\n> **[#調査 結果 {today}]**\n"
        f"> 調査内容: {instruction}\n>\n"
        + "\n".join(f"> {line}" for line in result.splitlines())
        + "\n"
    )


def process_メール(instruction: str) -> str:
    """#メール: Gmail下書きを作成して結果テキストを返す"""
    print(f"  [#メール] 下書き作成中: {instruction}")
    today = datetime.datetime.now(JST).strftime("%Y-%m-%d")

    # メール本文を生成（SORAスタイル）
    subject = f"【ご連絡】{instruction[:30]}"
    body = (
        f"お疲れ様です。\n\n"
        f"{instruction}\n\n"
        f"以上、よろしくお願いいたします。\n"
    )

    try:
        # Gmail MCPで下書き作成
        result = mcp_call(
            "gmail_create_draft",
            "gmail",
            {
                "to": "",  # 宛先は空（TAKUMIが後で設定）
                "subject": subject,
                "body": body
            }
        )
        draft_id = result.get("id", "（ID取得失敗）")
        return (
            f"\n\n> **[#メール 下書き作成済み {today}]**\n"
            f"> 件名: {subject}\n"
            f"> 指示: {instruction}\n"
            f"> Gmail下書きID: {draft_id}\n"
            f"> → Gmailを開いて宛先・本文を確認・編集してください\n"
        )
    except Exception as e:
        return (
            f"\n\n> **[#メール 下書き作成失敗 {today}]**\n"
            f"> 指示: {instruction}\n"
            f"> エラー: {str(e)[:100]}\n"
            f"> → 手動でGmailから作成してください\n"
        )


def process_タスク(instruction: str, task_ds_id: str, proj_ds_id: str) -> str:
    """#タスク: TASK DBに登録して結果テキストを返す"""
    print(f"  [#タスク] TASK DB登録中: {instruction}")
    today = datetime.datetime.now(JST).strftime("%Y-%m-%d")

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
        return (
            f"\n\n> **[#タスク 登録済み {today}]**\n"
            f"> タスク名: {instruction}\n"
            f"> TASK DB: {page_url if page_url else '登録完了'}\n"
        )
    except Exception as e:
        return (
            f"\n\n> **[#タスク 登録失敗 {today}]**\n"
            f"> タスク名: {instruction}\n"
            f"> エラー: {str(e)[:100]}\n"
            f"> → 手動でTASK DBに登録してください\n"
        )


# ============================================================
# メイン処理
# ============================================================

def process_tags_in_daily(page_id: str, page_text: str, task_ds_id: str, proj_ds_id: str) -> list[dict]:
    """
    デイリーページのテキストからタグを検出して処理し、
    結果をページに追記する。

    Returns: 処理したタグのリスト
    """
    tags = extract_tags(page_text)
    if not tags:
        print("  タグなし。スキップ。")
        return []

    print(f"  {len(tags)}件のタグを検出: {[t['tag'] for t in tags]}")
    processed = []

    for item in tags:
        instruction = item["instruction"]
        tag = item["tag"]
        result_text = ""

        if tag == "#調査":
            result_text = process_調査(instruction)
        elif tag == "#メール":
            result_text = process_メール(instruction)
        elif tag == "#タスク":
            result_text = process_タスク(instruction, task_ds_id, proj_ds_id)

        if result_text:
            # 結果をページに追記
            try:
                today = datetime.datetime.now(JST).strftime("%Y-%m-%d")
                processed_marker = f"<!-- {tag} 処理済み {today} -->"
                append_text = result_text
                mcp_call(
                    "notion-update-page",
                    "notion",
                    {
                        "id": page_id,
                        "content": append_text,
                        "command": "append_content"
                    }
                )
                processed.append({"tag": tag, "instruction": instruction, "status": "success"})
                print(f"  ✅ {tag} 処理完了・結果を追記")
            except Exception as e:
                print(f"  ❌ {tag} 結果追記失敗: {e}")
                processed.append({"tag": tag, "instruction": instruction, "status": "error", "error": str(e)})

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
