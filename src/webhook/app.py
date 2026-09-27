"""LINE Webhook。署名検証のうえ、送信元のグループIDをログに出力する(グループID取得用)。"""

import base64
import hashlib
import hmac
import json
import os

import boto3

_ssm = None
_secret = None


def ssm_client():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm


def get_channel_secret():
    global _secret
    if _secret is None:
        res = ssm_client().get_parameter(Name=os.environ["SECRET_PARAM"], WithDecryption=True)
        _secret = res["Parameter"]["Value"]
    return _secret


def verify_signature(secret, body, signature):
    if not signature:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


def _raw_body(event):
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    return body.encode()


def _response(status):
    return {"statusCode": status, "headers": {"Content-Type": "text/plain"}, "body": ""}


def lambda_handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    body = _raw_body(event)

    if not verify_signature(get_channel_secret(), body, headers.get("x-line-signature")):
        print(json.dumps({"message": "invalid signature"}))
        return _response(403)

    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return _response(400)

    for ev in payload.get("events", []):
        source = ev.get("source", {})
        print(json.dumps({
            "eventType": ev.get("type"),
            "sourceType": source.get("type"),
            "groupId": source.get("groupId"),
            "roomId": source.get("roomId"),
            "userId": source.get("userId"),
        }))

    return _response(200)
