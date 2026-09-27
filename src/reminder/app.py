"""締切日当日・履修登録開始日当日の通知を、全員宛メンション付きでLINEグループへpushする。"""

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import boto3

JST = ZoneInfo("Asia/Tokyo")
PUSH_URL = "https://api.line.me/v2/bot/message/push"
MAX_TEXT_LEN = 5000
WEEKDAYS = "月火水木金土日"
# textV2 の置換キー。メッセージ先頭で @All メンションに置き換わる
MENTION_KEY = "everyone"
# 同じ日の再試行で二重送信しないよう、送信先と日付から Retry-Key を決定的に生成する
RETRY_KEY_NAMESPACE = uuid.UUID("6f0c1b8e-6a57-4f7e-9d3c-2b1f6d9a4e10")

_ssm = None
_dynamodb = None
_param_cache = {}


def ssm_client():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm


def dynamodb_client():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.client("dynamodb")
    return _dynamodb


def get_param(name, decrypt=False):
    if name not in _param_cache:
        res = ssm_client().get_parameter(Name=name, WithDecryption=decrypt)
        _param_cache[name] = res["Parameter"]["Value"]
    return _param_cache[name]


def now_jst():
    return datetime.now(JST).date()


def _query_all(**kwargs):
    items = []
    while True:
        res = dynamodb_client().query(**kwargs)
        items.extend(res.get("Items", []))
        last_key = res.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs["ExclusiveStartKey"] = last_key


def _to_record(item):
    return {k: v["S"] for k, v in item.items() if "S" in v}


def query_deadlines(target):
    items = _query_all(
        TableName=os.environ["TABLE_NAME"],
        KeyConditionExpression="deadline_date = :d",
        ExpressionAttributeValues={":d": {"S": target.isoformat()}},
    )
    return [_to_record(i) for i in items]


def query_starts(target):
    items = _query_all(
        TableName=os.environ["TABLE_NAME"],
        IndexName=os.environ["GSI_NAME"],
        KeyConditionExpression="start_date = :d",
        ExpressionAttributeValues={":d": {"S": target.isoformat()}},
    )
    return [_to_record(i) for i in items]


def fmt_date(d):
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


def fmt_iso(s):
    try:
        return fmt_date(date.fromisoformat(s))
    except (TypeError, ValueError):
        return s


def build_message(today, due_today, starts_today):
    if not (due_today or starts_today):
        return None

    lines = [f"📅 {fmt_date(today)} のお知らせ"]
    if due_today:
        lines.append("【本日締切】")
        lines += [f"・{r['title']}" for r in due_today]
    if starts_today:
        lines.append("【本日履修登録開始】")
        lines += [f"・{r['title']}(締切 {fmt_iso(r.get('deadline_date'))})" for r in starts_today]
    return "\n".join(lines)


def _escape_braces(text):
    # textV2 では { } がプレースホルダ記号になるため、本文中の波括弧はエスケープする
    return text.replace("{", "{{").replace("}", "}}")


def build_text_v2(text):
    """先頭に @All メンションを付けた textV2 メッセージオブジェクトを返す。"""
    prefix = "{" + MENTION_KEY + "}\n"
    limit = MAX_TEXT_LEN - len(prefix)
    body = text
    cut = len(text)
    while len(_escape_braces(body)) > limit:
        cut -= len(_escape_braces(body)) - limit
        body = text[: cut - 2] + "\n…"
    return {
        "type": "textV2",
        "text": prefix + _escape_braces(body),
        "substitution": {
            MENTION_KEY: {"type": "mention", "mentionee": {"type": "all"}},
        },
    }


def push_message(token, to, text, retry_key):
    body = json.dumps({"to": to, "messages": [build_text_v2(text)]}).encode()
    req = urllib.request.Request(
        PUSH_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Line-Retry-Key": retry_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status
    except urllib.error.HTTPError as e:
        # 409: 同じ Retry-Key のリクエストが既に受理済み(再試行時の二重送信防止)
        if e.code == 409:
            print(json.dumps({"message": "already sent", "retryKey": retry_key}))
            return e.code
        raw = e.read().decode(errors="replace")
        try:
            detail = json.loads(raw)
        except ValueError:
            detail = raw
        print(json.dumps({
            "message": "LINE push failed",
            "status": e.code,
            "requestId": e.headers.get("x-line-request-id") if e.headers else None,
            "error": detail,
        }, ensure_ascii=False))
        raise
    except urllib.error.URLError as e:
        print(json.dumps({"message": "LINE push connection error", "reason": str(e.reason)}, ensure_ascii=False))
        raise


def lambda_handler(event, context):
    today = now_jst()
    due_today = query_deadlines(today)
    starts_today = query_starts(today)

    result = {
        "date": today.isoformat(),
        "due_today": len(due_today),
        "starts_today": len(starts_today),
        "sent": False,
    }

    text = build_message(today, due_today, starts_today)
    if text is None:
        print(json.dumps(result))
        return result

    group_id = get_param(os.environ["GROUP_ID_PARAM"])
    token = get_param(os.environ["TOKEN_PARAM"], decrypt=True)
    retry_key = str(uuid.uuid5(RETRY_KEY_NAMESPACE, f"{group_id}:{today.isoformat()}"))
    push_message(token, group_id, text, retry_key)

    result["sent"] = True
    print(json.dumps(result))
    return result
