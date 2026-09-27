
# nucb-reminder

MBAグループ向けのLINE締切リマインダーBot。

## 技術方針
- Python、AWS SAM(template.yaml)で構成
- 依存は標準ライブラリ+boto3のみ
- トークン・シークレットはSSM Parameter Store(/line-reminder/*)から取得。Secrets Managerは使わない
- IAMは最小権限
- 有料サービス(Secrets Manager、API Gateway等)は使わない
- AWSリソースは必ずtemplate.yamlで定義し、コンソールでの直接変更はしない

## 稼働仕様
- DynamoDBテーブル(deadlines)で、履修登録開始日(start_date)と締切日(deadline_date)を1レコードで管理する
  - パーティションキー: deadline_date、ソートキー: title、属性: start_date
  - GSI(start-date-index): パーティションキー: start_date、ソートキー: title
- 締切日の通知: 当日のみ
- 履修登録開始日の通知: 当日のみ
- 通知メッセージは全員宛メンション付き(textV2、mentionee type: all)で送信する
- 「本日募集開始」の表記は「本日履修登録開始」とする

## Webhook関数
- 削除せず残す
- 署名検証(X-Line-Signature、HMAC-SHA256)を必須とする
- グループID取得用に使う

## 登録機能
- LINEチャット経由の登録機能(方式A)は第2段階。初期版は固定の締切通知のみ