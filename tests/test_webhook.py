import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock

import pytest

SECRET = "channel-secret"


def sign(body: bytes) -> str:
    return base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


@pytest.fixture(autouse=True)
def ssm(webhook_app):
    client = MagicMock()
    client.get_parameter.return_value = {"Parameter": {"Value": SECRET}}
    webhook_app._ssm = client
    return client


def group_event_body():
    return json.dumps({
        "destination": "U000",
        "events": [{
            "type": "message",
            "source": {"type": "group", "groupId": "Cgroup123", "userId": "Uuser456"},
            "message": {"type": "text", "text": "hello"},
        }],
    }).encode()


def make_event(body: bytes, signature=None, b64=False):
    headers = {"content-type": "application/json"}
    if signature is not None:
        headers["x-line-signature"] = signature
    return {
        "headers": headers,
        "body": base64.b64encode(body).decode() if b64 else body.decode(),
        "isBase64Encoded": b64,
    }


def test_valid_signature_logs_group_id(webhook_app, ssm, capsys):
    body = group_event_body()
    res = webhook_app.lambda_handler(make_event(body, sign(body)), None)

    assert res["statusCode"] == 200
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert logged[0]["groupId"] == "Cgroup123"
    assert logged[0]["sourceType"] == "group"
    ssm.get_parameter.assert_called_once_with(Name="/line-reminder/channel-secret", WithDecryption=True)


def test_invalid_signature_rejected(webhook_app, capsys):
    body = group_event_body()
    res = webhook_app.lambda_handler(make_event(body, sign(b"tampered")), None)

    assert res["statusCode"] == 403
    assert "Cgroup123" not in capsys.readouterr().out


def test_missing_signature_rejected(webhook_app):
    body = group_event_body()
    assert webhook_app.lambda_handler(make_event(body), None)["statusCode"] == 403


def test_header_name_case_insensitive(webhook_app):
    body = group_event_body()
    event = make_event(body)
    event["headers"]["X-Line-Signature"] = sign(body)
    assert webhook_app.lambda_handler(event, None)["statusCode"] == 200


def test_base64_encoded_body(webhook_app, capsys):
    body = group_event_body()
    res = webhook_app.lambda_handler(make_event(body, sign(body), b64=True), None)

    assert res["statusCode"] == 200
    assert "Cgroup123" in capsys.readouterr().out


def test_verify_button_empty_events(webhook_app):
    body = json.dumps({"destination": "U000", "events": []}).encode()
    assert webhook_app.lambda_handler(make_event(body, sign(body)), None)["statusCode"] == 200


def test_secret_is_cached(webhook_app, ssm):
    body = group_event_body()
    webhook_app.lambda_handler(make_event(body, sign(body)), None)
    webhook_app.lambda_handler(make_event(body, sign(body)), None)
    ssm.get_parameter.assert_called_once()
