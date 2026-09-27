# nucb-reminder

MBAグループ向けの LINE 締切リマインダーBot。
毎朝 9:00(JST)に、DynamoDB に登録された科目の「締切日」と「履修登録開始日」を確認し、該当があれば LINE グループへ全員宛メンション付きで1通にまとめて通知します。

## 通知内容

| 種別 | タイミング |
|---|---|
| 締切日 | 当日のみ |
| 履修登録開始日 | 当日のみ |

該当がない日は送信しません。通知例:

```
@All
📅 12/4(金) のお知らせ
【本日締切】
・[F7] Designing Organizations
・[F7] Leading Global Business
【本日履修登録開始】
・[F8] Crisis Management and Business Continuity(締切 12/25(金))
```

## 構成

```
EventBridge Scheduler (毎日 9:00 JST)
  └─> ReminderFunction ──Query──> DynamoDB (deadlines)
            └──push (textV2, @All)──> LINE Messaging API ──> LINEグループ

LINE ──Webhook──> Lambda Function URL ──> WebhookFunction(署名検証 → groupId をログ出力)
```

| リソース | 内容 |
|---|---|
| `deadlines` テーブル | PK: `deadline_date` / SK: `title` / 属性: `start_date`(プロビジョンド 1 RCU/WCU) |
| `start-date-index` GSI | PK: `start_date` / SK: `title` |
| ReminderFunction | [src/reminder/app.py](src/reminder/app.py)。締切当日・履修登録開始当日を取得して push |
| WebhookFunction | [src/webhook/app.py](src/webhook/app.py)。`X-Line-Signature` を検証し、グループIDを CloudWatch Logs に出力(返信はしない) |

AWSリソースはすべて [template.yaml](template.yaml) で定義しています。コンソールで直接変更しないでください。

## 前提

- AWS CLI / AWS SAM CLI
- Python 3.13 以上
- LINE Developers の Messaging API チャネル(チャネルアクセストークン・チャネルシークレット)

## セットアップ

### 1. SSM パラメータを登録

トークン類はテンプレートに含めず、SSM Parameter Store から取得します。

```sh
aws ssm put-parameter --name /line-reminder/channel-access-token --type SecureString --value '<チャネルアクセストークン>'
aws ssm put-parameter --name /line-reminder/channel-secret       --type SecureString --value '<チャネルシークレット>'
aws ssm put-parameter --name /line-reminder/group-id             --type String       --value 'dummy'
```

`group-id` は手順 3 で取得した値に差し替えます。

### 2. デプロイ

```sh
sam build
sam deploy --guided   # 2回目以降は sam deploy
```

出力 `WebhookFunctionUrl` を LINE Developers の Webhook URL に設定し、「Webhookの利用」をオンにして「検証」を押します。

### 3. グループIDを取得

1. Bot を対象の LINE グループに招待し、グループで何か発言する
2. WebhookFunction のログからグループIDを確認する
   ```sh
   sam logs -n WebhookFunction --stack-name <スタック名> --tail
   ```
   `"sourceType": "group"` の行の `groupId`(`C` で始まる値)を控える
3. SSM に登録する
   ```sh
   aws ssm put-parameter --name /line-reminder/group-id --type String --overwrite --value '<groupId>'
   ```

### 4. 締切データを投入

[items.json](items.json) に `batch-write-item` 形式で科目を記載し、投入します。

```sh
aws dynamodb batch-write-item --request-items file://items.json
```

- 日付は `YYYY-MM-DD` 形式
- `batch-write-item` は1回25件までです。超える場合はファイルを分けてください
- 同じ `deadline_date` + `title` の組み合わせは上書きされます

## 動作確認

手動で通知を実行します。当日に該当する科目がない場合は送信されません。

```sh
aws lambda invoke --function-name <ReminderFunction の関数名> /dev/stdout
```

同じグループ・同じ日の送信には同一の `X-Line-Retry-Key` を付けているため、同日に2回実行しても2通目は LINE 側で重複として扱われ送信されません(ログに `already sent` と出力されます)。

## 開発

依存は標準ライブラリ + boto3 のみです。テストは pytest で、AWS・LINE への通信はすべてモックしています。

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

テンプレートの検証:

```sh
sam validate --lint
```

## 注意事項

- グループへの push は、グループの人数分がメッセージ通数としてカウントされます。無料枠(月200通)に注意してください
- `@All` メンションはアカウントやグループの設定によって制限される場合があります
- API Gateway・Secrets Manager などの有料サービスは使わない方針です
- LINE チャット経由での締切登録機能は第2段階で対応予定です
