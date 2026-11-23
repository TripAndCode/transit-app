# 自治体・研究室向けデプロイ手順: 1 台のマシンで動かす

[English](deploy-standalone.en.md)

Docker が動く 1 台のマシンで、選んだバスのフィードについてアプリ全体が動きます。クラウドのアカウントも、オブジェクトストレージも、LLM のキーも要りません。閲覧者のアカウントも要りません。

## 必要なもの

- Docker（Compose プラグイン付き）と `git`。Linux、macOS、Windows（WSL）のいずれでも構いません。
- フィードと時刻表の URL への外向き HTTPS。外部からマシンへの接続は不要です。
- 事業者ごとに公開 URL を 2 つ: **GTFS-RT TripUpdates** フィードと **GTFS（GTFS-JP）静的 zip**。既知のフィードは [`feeds/`](../feeds/README.md) にあり、判定が `accept` の行は、その日付にプローブを通っています。行の `realtime_url` を手順 3 の `--feed-url` に、`static_url` を `--static-url` に使います。

## 1. クローンと設定

```bash
git clone https://github.com/TripAndCode/transit-app.git && cd transit-app
cp .env.example .env
```

`.env` はそのままでも動きます。必要なときだけ次の変数を編集します。

| 変数 | いつ |
| --- | --- |
| `DEFAULT_ADMIN_USERNAME`、`DEFAULT_ADMIN_PASSWORD`、`SESSION_SIGNING_KEY` | 管理者としてサインインするとき（手順 4）。3 つとも設定します（管理者の 2 行はコメントを外します）。キーは `openssl rand -hex 32` で作ります。 |
| `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`GITHUB_CLIENT_ID`、`GITHUB_CLIENT_SECRET`、`ADMIN_EMAILS`、`SESSION_SIGNING_KEY` | ローカル管理者の代わりに Google と GitHub でサインインする場合。クライアント用の 4 変数とキーがすべて必要で、`ADMIN_EMAILS` に管理者のメールアドレスを列挙します。列挙しないと、OAuth のサインインはすべて拒否されます。説明は `.env.example` にあります。 |
| `POLL_INTERVAL_SEC` | ポーリング間隔の秒数（既定 60）。 |
| `STANDALONE_PORT`、`PUBLIC_BASE_URL` | 8000 以外のホスト側ポートを使うとき（`127.0.0.1` のみ）。両方を設定します: `STANDALONE_PORT=9000`、`PUBLIC_BASE_URL=http://localhost:9000`。サインインは `PUBLIC_BASE_URL` が示すオリジンからのリクエストだけを受け付けます。 |
| `FORWARDED_HOPS` | アプリの前にあるリバースプロキシの段数（既定 0）。「自動化されていないこと」を参照してください。 |
| `POSTGRES_PASSWORD`、`CLICKHOUSE_PASSWORD` | Docker ネットワークに他者が届く場合は `transit` から変更します。データベースは作成時のパスワードを保持するため、初回起動の前に設定します。 |

Oracle、R2、天気、LLM の設定は無視して構いません。`GEMINI_API_KEY` と `OPENAI_API_KEY` は空のままにします。その場合、Ask ページのモデルによる回答だけが無効になり、ほかは動きます。

## 2. 起動

```bash
docker compose --profile standalone up -d --build app poller
```

`app poller` を指定すると、スタンドアロン用の構成だけが起動します。専用の Postgres と ClickHouse（ホストのポートなし、専用ボリューム）、スキーマ更新のステップ、アプリ、ポーラーです。`--profile standalone up` だけにしないでください。開発用の 2 つのデータベースまで起動します。初回のビルドには数分かかります。完了したら `PUBLIC_BASE_URL` が示す URL（既定は <http://localhost:8000>）を開きます。ページは表示されますが、事業者はまだ登録されていません。

## 3. 事業者を追加する

```bash
docker compose --profile standalone exec app python gtfs_pipeline.py add_agency \
  --feed-url 'https://example.test/trip_updates.pb' --static-url 'https://example.test/gtfs.zip'
```

先に `--dry-run` を付けると、判定だけを表示して何も書き込みません。このコマンドは時刻表をダウンロードし、フィードを 120 秒間（`--probe-seconds`）ポーリングして、フィードの StopTimeUpdate が `(trip_id, stop_sequence)` で時刻表に結合できるかを確認します。URL は `&` を含むことが多いので、引用符を付けたままにします。

出力の末尾を読みます。成功すると `verdict: accept`、事業者名、`agency_id` が表示され、時刻表の読み込みまで済んでいます。`accept` と `accept_empty` 以外の判定では何も追加されません。

| 判定 | 意味と対処 |
| --- | --- |
| `accept` | StopTimeUpdate の 80% 以上が結合しました。`static_join` で追加されます。 |
| `accept_empty` | `--accept-empty` を付けたときだけ: プローブ中にフィードが何も送りませんでした（運行時間外など）。未検証のまま追加されます。 |
| `name_required` | `agency.txt` に複数の事業者があるか名前がありません。`--name "..."` を付けて再実行します。 |
| `feed_taken` | そのフィード URL はすでに事業者として登録されています。 |
| `url_rejected` | URL が http(s) ではない、プライベートアドレスを指している、またはホスト名を解決できません。 |
| `static_unavailable`、`static_unreadable`、`static_incomplete` | zip をダウンロードできない、読めない、または `trips.txt`・`stops.txt`・`stop_times.txt` の行がありません。 |
| `feed_unreachable`、`not_a_feed`、`empty` | フィードが応答しない、GTFS-RT ではない、またはエンティティがありません。運行時間中に再実行します。 |
| `no_trip_updates`、`no_trip_ids`、`no_stop_time_updates` | TripUpdate がない、または `trip_id` や停車時刻がありません。 |
| `stop_id_only` | StopTimeUpdate が停留所を示すだけで `stop_sequence` がなく、結合できません。 |
| `low_join` | 結合したのが 80% 未満です。時刻表とフィードが食い違っています。出力に一致しなかった例が示されます。 |

判定のあとに具体的な理由が出力されます。拒否では何も書き込まれず、ポーラーが再試行することもありません。原因を直してから、もう一度コマンドを実行します。`--dry-run` を付けた場合は、付けずに再実行すると事業者が追加されます。保存先が読み込みを受け付けられないために何も追加されなかった、と出力された場合は、`docker compose --profile standalone logs migrate` を確認してから、コマンドをもう一度実行します。事業者は追加されたが時刻表を読み込めなかった、と出力された場合は、`docker compose --profile standalone exec app python gtfs_pipeline.py refresh-static --agency-id <id>` を実行します。

列を自分で指定したい場合（別の戦略、`trip_id_pattern`、固定の `agency_id`）は、`agencies.csv` と同じ列の CSV を書き、`docker compose --profile standalone cp my_agency.csv app:/tmp/my_agency.csv` でコピーしてから、上の `add_agency` と同じ要領で `seed_agencies /tmp/my_agency.csv` と `refresh-static` を実行します。この方法ではプローブを行いません。

## 4. 管理者としてサインインする

閲覧にサインインは要りません。管理者は `/login` から管理ページ（Agencies ページで戦略を編集できます）に入ります。ローカル管理者を使うには、`.env` に `DEFAULT_ADMIN_USERNAME`、`DEFAULT_ADMIN_PASSWORD` と本物の `SESSION_SIGNING_KEY` を設定し（3 つとも必要です。キーがないとアプリは起動を拒否し、理由は `docker compose --profile standalone logs app` に出ます）、`docker compose --profile standalone up -d app` を実行します。Google や GitHub を使う方法は `.env.example` に説明があります。

## 5. データはいつ出るか

ポーラーはサイクルごとに事業者の一覧を読むので、追加から 1 分ほどで拾われます。「ライブ」画面（ライブの遅延）には、`add_agency` から数分以内にその日の便が表示されます。

「概況」（Pulse）と「レポート」は日次の集計を読むため、その日が終わってから表示されます。最初のポーリングの後に迎える最初の JST 0 時を過ぎるまでは、データが 0 件のままです。これは異常ではありません。その後、UTC の日が終わると、アーカイブがその日を置き換えます。運行時間外のフィードは、最初の便が走るまで表示するものがありません。

進み具合は `docker compose --profile standalone logs -f poller` で確認できます。

## 6. バックアップ

すべては 3 つの名前付きボリュームにあります。`standalone_pgdata`（事業者、時刻表、集計）、`standalone_chdata`（データとしてのポーリング結果）、`standalone_archive`（生のポーリング結果。事業者ごと・日ごとに tarball 1 つ）です。Compose はプロジェクト名（チェックアウトのディレクトリ名）を各ボリュームの前に付けます。コミットやイメージのビルドに紛れ込まないよう、バックアップはチェックアウトの外に置きます。

```bash
B=${BACKUP_DIR:-$HOME/transit-backup}
P=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' $(docker compose --profile standalone ps -a -q standalone-db))
docker compose --profile standalone stop app poller standalone-clickhouse standalone-db
mkdir -p "$B"
for v in $(docker volume ls -q --filter label=com.docker.compose.project=$P --filter name=standalone_); do
  docker run --rm -v "$v":/data:ro -v "$B":/backup alpine tar czf "/backup/$v.tgz" -C /data .
done
docker compose --profile standalone up -d app poller
```

`P` はこのチェックアウトの Compose プロジェクトなので、ループが触れるのはその 3 つのボリュームだけです。停止中のポーラーは何も記録しません。短い停止は、その長さの欠測になります。バックアップのディレクトリはマシンの外にコピーしてください。アーカイブのボリュームは削除されず、事業者ごと・日ごとに tarball 1 つずつ増えます。

## 7. アップグレード

```bash
git pull
docker compose --profile standalone up -d --build app poller
```

アプリとポーラーが再起動する前にスキーマ更新のステップ（`gtfs_pipeline.py schema-up`）が走るので、マイグレーションは自動で適用されます。スキーマが変わるリリースの前にはバックアップを取ってください。すでに古い履歴を捨てたデータベースで、保存済みのデータを削除するマイグレーションがあると、スキーマ更新のステップが拒否します。`up` が失敗を報告し、`app` はそのステップを待つので起動しません。理由は `docker compose --profile standalone logs migrate` で読めます。バックアップを取ったうえで、そのマイグレーションのバージョンを `.env` の `MIGRATE_ALLOW_DESTRUCTIVE_UP` に書き、`up` をもう一度実行します。

## 8. 日次のステップを監視する

ポーラーは日次のステップの結果を [healthchecks.io](https://healthchecks.io) のチェック（同じ形式の ping を受け付ける他のサービスでも構いません）に報告できます。このステップは終わった日を履歴とレポートに移すので、止まるとレポートが進まなくなります。

1. 期間（Period）1 日、猶予（Grace）2 時間のチェックを作ります。
2. その ping URL を `.env` に `POLLER_PING_URL=<URL>` と書き、ポーラーを再起動します: `docker compose --profile standalone up -d poller`。
3. ポーラーは最初のポーリングの直後にステップを 1 回実行し、それがどのステージも失敗せずに終わるとチェックに ping が記録されます。最初のポーリングで何も保存されなかったとき（機関がまだない、またはすべてのフィードが失敗した）はステップを実行せず、次の UTC 0 時の後に最初のステップが走ります。届かない場合は、ポーラーのログに理由（失敗したステージ、または送れなかった ping）が出ます。URL はログに出ません。

ポーラーは、どのステージも失敗せずにステップが終わるたびに URL に ping します。通常は JST 0 時の後と UTC 0 時（JST 9 時）の後の 1 日 2 回です。あるステージが再試行（約 2 時間分）をすべて失敗すると `<URL>/fail` に ping し、チェックはすぐに通知します。時刻表が読み込まれていない機関は ingest をスキップし、これは再試行では直らないので、そのステップはすぐに `<URL>/fail` に ping します。`refresh-static`（3 節）で時刻表を読み込んでください。ポーラーが止まる、またはステップが終わらないと ping が途絶え、最後の ping から 26 時間後にチェックが通知します。失敗の後は、後日のステップがどのステージも失敗せずに終わると、チェックは正常に戻ります。

## 9. サイトを外から監視する

`/health/ready` は、アプリが両方のデータベースに問い合わせできるときに 200 を返します。できないときは 503 を返し、失敗した方を示します:

```json
{"status": "unavailable", "postgres": "ok", "clickhouse": "down"}
```

各データベースの応答は 5 秒まで待つので、どちらかが固まっていてもこのエンドポイントは応答します。`/health` はアプリのプロセスが応答することしか示さず、`/api/config` は Postgres が止まっていても応答し続けるので、どちらもサイトが動いていることを示しません。

1. このマシンの外で動く HTTP の死活監視（ホスティングされたサービス、または別のホストからのチェック）を、リバースプロキシ経由で `https://<ホスト名>/health/ready` に向けます。
2. 1 分ごとにチェックし、5 分間失敗が続いたら通知するように設定します。チェックの間隔が長いと、通知もその分遅れます。

監視は別の場所で動くので、このマシン、そのネットワーク、リバースプロキシが止まったときにも通知します。これらはマシン上の何からも報告できません。

アプリは起動時にだけ ClickHouse に接続します。そのとき ClickHouse に届かなかった場合、ClickHouse が戻った後も、アプリを再起動するまで `/health/ready` と ClickHouse を読むページは失敗し続けます: `docker compose --profile standalone restart app`。

## 自動化されていないこと

- 事業者が再公開した時刻表: `docker compose --profile standalone exec app python gtfs_pipeline.py refresh-static` を再実行します。
- フルデプロイの定期ジョブが行う天気と予測の処理。
- tarball やバックアップをマシンの外へアップロードすること、およびアーカイブの整理。
- TLS と公開ホスト名: アプリは `127.0.0.1` だけで待ち受けます。自前のリバースプロキシを前段に置き、`.env` の `PUBLIC_BASE_URL` をその公開アドレスに、`FORWARDED_HOPS` をプロキシの段数に設定してください。

## トラブルシューティング

- `docker compose --profile standalone ps -a` で `app` と `poller` が動いているか、再起動を繰り返していないかを確認できます。
- `app` や `poller` が再起動を繰り返す、または起動しない場合は、`docker compose --profile standalone logs app`（または `poller`）を読みます。`SESSION_SIGNING_KEY` がない、または既定値のままだと、ここに出ます。
- 取り込みが遅れた場合、ポーラーのログに各回が読む tarball が出ます。事業者のアーカイブ全体を手で取り込むには、`docker compose --profile standalone exec poller python gtfs_pipeline.py ingest /app/archive/<id>/rt --agency-id <id>` を実行します。
