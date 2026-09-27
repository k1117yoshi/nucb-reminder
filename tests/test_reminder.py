import io
import json
import urllib.error
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

PARAMS = {
    "/line-reminder/channel-access-token": "TOKEN",
    "/line-reminder/group-id": "C123",
}


def item(deadline, title, start=None):
    i = {"deadline_date": {"S": deadline}, "title": {"S": title}}
    if start:
        i["start_date"] = {"S": start}
    return i


@pytest.fixture
def ssm(reminder_app):
    client = MagicMock()
    client.get_parameter.side_effect = lambda Name, WithDecryption: {"Parameter": {"Value": PARAMS[Name]}}
    reminder_app._ssm = client
    return client


@pytest.fixture
def ddb(reminder_app):
    """(IndexName有無, 対象日) -> Items の辞書で応答を定義する。"""
    client = MagicMock()
    client.data = {}

    def query(**kwargs):
        key = (kwargs.get("IndexName"), kwargs["ExpressionAttributeValues"][":d"]["S"])
        return {"Items": client.data.get(key, [])}

    client.query.side_effect = query
    reminder_app._dynamodb = client
    return client


@pytest.fixture
def urlopen(reminder_app, monkeypatch):
    mock = MagicMock()
    mock.return_value.__enter__.return_value.status = 200
    monkeypatch.setattr(reminder_app.urllib.request, "urlopen", mock)
    return mock


def test_now_jst_crosses_date_boundary(reminder_app, monkeypatch):
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 27, 23, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(reminder_app, "datetime", FakeDatetime)
    assert reminder_app.now_jst() == date(2026, 9, 28)


def test_queries_today_deadline_and_gsi(reminder_app, ddb, ssm, urlopen, monkeypatch):
    monkeypatch.setattr(reminder_app, "now_jst", lambda: date(2026, 9, 27))
    reminder_app.lambda_handler({}, None)

    calls = [c.kwargs for c in ddb.query.call_args_list]
    assert len(calls) == 2
    assert calls[0]["KeyConditionExpression"] == "deadline_date = :d"
    assert calls[0]["ExpressionAttributeValues"] == {":d": {"S": "2026-09-27"}}
    assert "IndexName" not in calls[0]
    assert calls[1]["IndexName"] == "start-date-index"
    assert calls[1]["KeyConditionExpression"] == "start_date = :d"
    assert calls[1]["ExpressionAttributeValues"] == {":d": {"S": "2026-09-27"}}
    assert all(c["TableName"] == "deadlines" for c in calls)


def test_query_paginates(reminder_app, ddb):
    responses = [
        {"Items": [item("2026-09-27", "A")], "LastEvaluatedKey": {"k": 1}},
        {"Items": [item("2026-09-27", "B")]},
    ]
    ddb.query.side_effect = responses
    records = reminder_app.query_deadlines(date(2026, 9, 27))

    assert [r["title"] for r in records] == ["A", "B"]
    assert ddb.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {"k": 1}


def test_build_message_all_sections(reminder_app):
    text = reminder_app.build_message(
        date(2026, 9, 27),
        [{"title": "課題A"}],
        [{"title": "科目C", "deadline_date": "2026-10-10"}],
    )
    assert text == (
        "📅 9/27(日) のお知らせ\n"
        "【本日締切】\n・課題A\n"
        "【本日履修登録開始】\n・科目C(締切 10/10(土))"
    )
    assert "3日前" not in text
    assert "募集開始" not in text


def test_build_message_omits_empty_sections(reminder_app):
    text = reminder_app.build_message(date(2026, 9, 27), [], [{"title": "B", "deadline_date": "2026-10-01"}])
    assert "【本日締切】" not in text
    assert "【本日履修登録開始】" in text


def test_build_message_none_when_empty(reminder_app):
    assert reminder_app.build_message(date(2026, 9, 27), [], []) is None


def test_text_v2_mentions_all_at_head(reminder_app):
    msg = reminder_app.build_text_v2("本文")
    assert msg == {
        "type": "textV2",
        "text": "{everyone}\n本文",
        "substitution": {"everyone": {"type": "mention", "mentionee": {"type": "all"}}},
    }


def test_text_v2_escapes_braces(reminder_app):
    msg = reminder_app.build_text_v2("課題{A}")
    assert msg["text"] == "{everyone}\n課題{{A}}"


def test_text_v2_truncates(reminder_app):
    msg = reminder_app.build_text_v2("\n".join("・" + "{x}" * 50 for _ in range(100)))
    assert len(msg["text"]) <= reminder_app.MAX_TEXT_LEN
    assert msg["text"].endswith("\n…")
    assert msg["text"].startswith("{everyone}\n")


def test_no_push_when_nothing_due(reminder_app, ddb, ssm, urlopen, monkeypatch):
    monkeypatch.setattr(reminder_app, "now_jst", lambda: date(2026, 9, 27))
    result = reminder_app.lambda_handler({}, None)

    assert result["sent"] is False
    urlopen.assert_not_called()
    ssm.get_parameter.assert_not_called()


def test_pushes_one_combined_message(reminder_app, ddb, ssm, urlopen, monkeypatch):
    monkeypatch.setattr(reminder_app, "now_jst", lambda: date(2026, 9, 27))
    ddb.data = {
        (None, "2026-09-27"): [item("2026-09-27", "課題A"), item("2026-09-27", "課題A2")],
        ("start-date-index", "2026-09-27"): [item("2026-10-10", "科目C", start="2026-09-27")],
    }
    result = reminder_app.lambda_handler({}, None)

    assert result == {"date": "2026-09-27", "due_today": 2, "starts_today": 1, "sent": True}
    urlopen.assert_called_once()
    req = urlopen.call_args.args[0]
    assert req.full_url == "https://api.line.me/v2/bot/message/push"
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer TOKEN"
    assert req.get_header("X-line-retry-key")
    body = json.loads(req.data)
    assert body["to"] == "C123"
    assert len(body["messages"]) == 1
    message = body["messages"][0]
    assert message["type"] == "textV2"
    assert message["text"].startswith("{everyone}\n")
    assert message["substitution"]["everyone"]["mentionee"] == {"type": "all"}
    for title in ("課題A", "課題A2", "科目C"):
        assert title in message["text"]
    ssm.get_parameter.assert_any_call(Name="/line-reminder/channel-access-token", WithDecryption=True)


def test_retry_key_is_stable_for_same_day(reminder_app, ddb, ssm, urlopen, monkeypatch):
    monkeypatch.setattr(reminder_app, "now_jst", lambda: date(2026, 9, 27))
    ddb.data = {(None, "2026-09-27"): [item("2026-09-27", "A")]}
    reminder_app.lambda_handler({}, None)
    reminder_app.lambda_handler({}, None)

    keys = [c.args[0].get_header("X-line-retry-key") for c in urlopen.call_args_list]
    assert keys[0] == keys[1]


def _http_error(code, body=b'{"message":"err"}'):
    return urllib.error.HTTPError(
        "https://api.line.me/v2/bot/message/push", code, "err",
        {"x-line-request-id": "req-1"}, io.BytesIO(body),
    )


def test_push_logs_and_raises_on_api_error(reminder_app, urlopen, capsys):
    error = {"message": "The request body has 1 error(s)", "details": [{"message": "invalid", "property": "messages[0].text"}]}
    urlopen.side_effect = _http_error(400, json.dumps(error).encode())
    with pytest.raises(urllib.error.HTTPError):
        reminder_app.push_message("TOKEN", "C123", "hi", "key")

    log = json.loads(capsys.readouterr().out.strip())
    assert log["status"] == 400
    assert log["requestId"] == "req-1"
    assert log["error"] == error


def test_push_logs_non_json_error_body(reminder_app, urlopen, capsys):
    urlopen.side_effect = _http_error(500, b"Internal Server Error")
    with pytest.raises(urllib.error.HTTPError):
        reminder_app.push_message("TOKEN", "C123", "hi", "key")
    assert json.loads(capsys.readouterr().out.strip())["error"] == "Internal Server Error"


def test_push_logs_and_raises_on_connection_error(reminder_app, urlopen, capsys):
    urlopen.side_effect = urllib.error.URLError("timed out")
    with pytest.raises(urllib.error.URLError):
        reminder_app.push_message("TOKEN", "C123", "hi", "key")
    assert "timed out" in capsys.readouterr().out


def test_push_treats_409_as_already_sent(reminder_app, urlopen):
    urlopen.side_effect = _http_error(409)
    assert reminder_app.push_message("TOKEN", "C123", "hi", "key") == 409
