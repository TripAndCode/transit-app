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

空の状態ではなく既存の R2 アーカイブの履歴から始める場合は、この節と次の節の代わりに 9 節に従います。

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

`scripts/standalone_backup.sh` は、スタックを動かしたまま 2 つのデータベースをバックアップします。Postgres は `pg_dump` で、ClickHouse はそれ自身の `BACKUP` で取ります。実行のたびに `BACKUP_DIR` の下に UTC 時刻を名前にしたディレクトリを作り、新しいものから `BACKUP_KEEP` 個を残します。ホストの crontab（`crontab -e`）から毎晩実行します。

```cron
15 3 * * * cd /path/to/transit-app && PATH=/usr/local/bin:/usr/bin:/bin scripts/standalone_backup.sh >>"$HOME/transit-backup.log" 2>&1
```

cron の `PATH` には Docker が入っていないことがあるので、`PATH=` には `command -v docker` が表示するディレクトリを含めてください。

スクリプトは `BACKUP_DIR`、`BACKUP_KEEP`、`BACKUP_PING_URL` を環境変数から、なければ `.env` から読みます。`BACKUP_STORE_*` は `.env` 自体に書いてください。アップロードは `.env` だけを読むコンテナの中で実行されます。`.env` では値を引用符で囲まずに書きます。

| 設定 | 役割 |
| --- | --- |
| `BACKUP_DIR` | バックアップの置き場所。絶対パスか `~/` で始まるパスで書きます。既定は `~/transit-backup`。コミットやイメージのビルドに紛れ込まないよう、チェックアウトの外に置きます。 |
| `BACKUP_KEEP` | このマシンに残す完全なバックアップの数。既定は 7。クラッシュや停電で途中で止まった実行が残した不完全なディレクトリは数えず、残すものすべてより古くなった時点で削除します。 |
| `BACKUP_PING_URL` | 期間 1 日、猶予 2 時間の [healthchecks.io](https://healthchecks.io) 形式のチェック。バックアップが終わると ping し、失敗すると `<URL>/fail` に ping するので、失敗した夜や実行されなかった夜は通知されます。 |
| `BACKUP_STORE_ENDPOINT`、`BACKUP_STORE_BUCKET`、`BACKUP_STORE_ACCESS_KEY_ID`、`BACKUP_STORE_SECRET_ACCESS_KEY` | 各バックアップを Cloudflare R2 などの S3 互換ストレージの `<BACKUP_STORE_PREFIX>/<時刻>/` にもアップロードします（プレフィックスの既定は `backups`）。キーにはそのバケットへの書き込み権限が要ります。古いバックアップは、バケット自身のライフサイクルルールで削除するよう設定してください。アップロードに失敗してもローカルのコピーは残り、`/fail` に ping します。 |

ClickHouse はバックアップをいったん自身のボリュームに書き、スクリプトがそれを外へコピーします。そのため実行中は、そのディスクにバックアップ 1 つ分の空きが要ります。

各バックアップは 2 つのデータベースの全体を含むので、履歴とともに大きくなります。アーカイブのボリューム（生のポーリング結果）は含みません。取り込み済みの内容は ClickHouse が持っています。アーカイブも残したいとき、またはマシン全体をコピーするときは、スタックを止めて 3 つのボリュームをコピーします。

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

`P` はこのチェックアウトの Compose プロジェクトなので、ループが触れるのはその 3 つのボリュームだけです。停止中のポーラーは何も記録しません。短い停止は、その長さの欠測になります。アーカイブのボリュームは削除されず、事業者ごと・日ごとに tarball 1 つずつ増えます。

### 復元

バックアップは空のデータベースにだけ復元できます。新しいマシンか、このチェックアウトの standalone ボリュームを削除した後です。どちらかのデータベースにすでにテーブルがあるとスクリプトは拒否するので、データを上書きすることはありません。チェックアウトで次を実行します。

```bash
scripts/standalone_restore.sh ~/transit-backup/20260101T031500Z
docker compose --profile standalone up -d app poller
```

スクリプトは 2 つのデータベースだけを起動し、両方を復元します。続く `up` がスキーマ更新のステップを実行してバックアップより新しいマイグレーションを適用し、アプリとポーラーを起動します。バケットから復元するときは、まず `<プレフィックス>/<時刻>/` のフォルダ 1 つを、ストレージ事業者のツールでローカルのディレクトリにダウンロードしてください。バックアップから復元までの間のポーリング結果は欠けます。

復元が途中で失敗すると Postgres にはすでにバックアップが入っていることがあり、スクリプトは再実行を拒否します。アーカイブは残したまま 2 つのデータベースのボリュームを削除し、もう一度実行してください。

```bash
P=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' $(docker compose --profile standalone ps -a -q standalone-db))
docker compose --profile standalone down
docker volume rm "${P}_standalone_pgdata" "${P}_standalone_chdata"
```

## 7. アップグレード

```bash
git pull
docker compose --profile standalone up -d --build app poller
```

アプリとポーラーが再起動する前にスキーマ更新のステップ（`gtfs_pipeline.py schema-up`）が走るので、マイグレーションは自動で適用されます。すでに古い履歴を捨てたデータベースで、保存済みのデータを削除するマイグレーションがあると、スキーマ更新のステップが拒否します。`up` が失敗を報告し、`app` はそのステップを待つので起動しません。理由は `docker compose --profile standalone logs migrate` で読めます。バックアップを取ったうえで、そのマイグレーションのバージョンを `.env` の `MIGRATE_ALLOW_DESTRUCTIVE_UP` に書き、`up` をもう一度実行します。

1 回ごとの変更を小さく保つため、アップグレードは 2 週間に 1 回ほど行い、必要な修正があればそれより早く行います。毎回、その前にスキーマが変わるかどうかを確認します:

```bash
git fetch
git diff --stat HEAD origin/main -- db/
```

ファイルが表示された場合は、`scripts/standalone_backup.sh`（6 節）を実行し、そのバックアップでアップグレードをリハーサルしてから、ここで実行します。別のディレクトリにある 2 つ目のチェックアウトは独立した Compose プロジェクトになり、専用のボリュームを持ちます。両方のデータベースをもう 1 組置けるだけのディスクが必要です:

```bash
git clone https://github.com/TripAndCode/transit-app.git ~/transit-rehearsal && cd ~/transit-rehearsal
cp /path/to/transit-app/.env .env
```

コピーした `.env` で `STANDALONE_PORT=9000` と `PUBLIC_BASE_URL=http://localhost:9000` を設定し、`COMPOSE_PROJECT_NAME` の行があれば消します。残っていると、コピーがこのチェックアウトと同じプロジェクトになります。最新のバックアップを復元し、アプリだけを起動します。ポーラーは起動しません:

```bash
scripts/standalone_restore.sh ~/transit-backup/<最新>
docker compose --profile standalone up -d --build app
docker compose --profile standalone logs migrate
```

スキーマ更新のステップが成功し、<http://localhost:9000> に履歴が表示されたら、このチェックアウトをアップグレードします。その後、`~/transit-rehearsal` でだけ `docker compose --profile standalone down -v` を実行してそのチェックアウトのボリュームを削除し、ディレクトリも削除します。このチェックアウトで同じコマンドを実行すると、データが消えます。

## 8. 日次のステップを監視する

ポーラーは日次のステップの結果を [healthchecks.io](https://healthchecks.io) のチェック（同じ形式の ping を受け付ける他のサービスでも構いません）に報告できます。このステップは終わった日を履歴とレポートに移すので、止まるとレポートが進まなくなります。

1. 期間（Period）1 日、猶予（Grace）2 時間のチェックを作ります。
2. その ping URL を `.env` に `POLLER_PING_URL=<URL>` と書き、ポーラーを再起動します: `docker compose --profile standalone up -d poller`。
3. ポーラーは最初のポーリングの直後にステップを 1 回実行し、それがどのステージも失敗せずに終わるとチェックに ping が記録されます。最初のポーリングで何も保存されなかったとき（機関がまだない、またはすべてのフィードが失敗した）はステップを実行せず、次の UTC 0 時の後に最初のステップが走ります。届かない場合は、ポーラーのログに理由（失敗したステージ、または送れなかった ping）が出ます。URL はログに出ません。

ポーラーは、どのステージも失敗せずにステップが終わるたびに URL に ping します。通常は JST 0 時の後と UTC 0 時（JST 9 時）の後の 1 日 2 回です。あるステージが再試行（約 2 時間分）をすべて失敗すると `<URL>/fail` に ping し、チェックはすぐに通知します。analyze が終わってもその機関の集計が最新の締まった日に追いついていない場合も、analyze の失敗として扱います。時刻表が読み込まれていない機関は ingest をスキップし、これは再試行では直らないので、そのステップはすぐに `<URL>/fail` に ping します。`refresh-static`（3 節）で時刻表を読み込んでください。ポーラーが止まる、またはステップが終わらないと ping が途絶え、最後の ping から 26 時間後にチェックが通知します。失敗の後は、後日のステップがどのステージも失敗せずに終わると、チェックは正常に戻ります。

## 9. R2 アーカイブから始める

この節は、収集器がすでにフィードを R2 バケットに保存している運用者向けです（[deploy-railway.md](deploy-railway.md) の Oracle の収集器など）。それ以外の方は読み飛ばしてください。新しいデータベースを空から始めるのではなく、バケットにある直近の日々で埋めます。2 節と 3 節の代わりに、ポーラーを初めて起動する前に 1 回だけ行います。ポーラーは同じ日を自分のポーリングからも保存するので、そのあとでは日次のステップが同じ日をもう一度取り込んでしまいます。

1. バケットの設定を `.env` に書きます: `OBJECT_STORE_ENDPOINT`、`OBJECT_STORE_BUCKET`、`OBJECT_STORE_ACCESS_KEY_ID`、`OBJECT_STORE_SECRET_ACCESS_KEY`。読み取り専用のキーで足ります。
2. アプリだけを起動します: `docker compose --profile standalone up -d --build app`。
3. 事業者を `seed_agencies`（3 節）で追加し、それぞれにバケットが保存に使っている `agency_id`（`rt/<agency_id>/`）を付けます。このプロジェクト自身のバケットなら、`agencies.csv` の行がそれです。`add_agency` も `refresh-static` も実行しないでください。追い上げは最初の日に有効だった時刻表を自分で読み込み、取り込みに時刻表を結合する事業者について、それより新しい時刻表がすでに読み込まれていると拒否します。
4. 計画を読みます。何も書き込みません:

   ```bash
   docker compose --profile standalone run --rm app python scripts/catch_up_from_r2.py --seed-days 7
   ```

   `--seed-days` は何日前（JST）から始めるかです。`--agency-id <id>` を付けると（繰り返し可）、その事業者だけに絞ります。計画は、バケットにアーカイブがない最初の日でその事業者を止めます。`--allow-gaps` を付けると、そうした日を飛ばします。
5. 同じコマンドに `--apply --analyze` を付けて実行します。データベース名（`transit`）の入力を求められます。コンテナ内にダウンロードし、各日をその日に有効だった時刻表と結合して取り込み、最後に集計を作ります。ダウンロードしたファイルは `--rm` によりコンテナごと消えます。計画が、ある事業者の時刻表は手で読み込むよう残すと示した場合（取り込みに時刻表を使わない事業者）は、ここでその事業者に `refresh-static --agency-id <id>` を実行します（3 節）。
6. `.env` から 4 つの設定を消します。このプロファイルでほかに読むものはなく、設定されている間はポーラーがログに警告を出します。そのうえでポーラーを起動します: `docker compose --profile standalone up -d app poller`。

最初の日は JST 9 時以降の分だけを持ちます。ポーラーを起動した日は、最初のポーリングより前の時間が欠けます。

## 自動化されていないこと

- 事業者が再公開した時刻表: `docker compose --profile standalone exec app python gtfs_pipeline.py refresh-static` を再実行します。
- フルデプロイの定期ジョブが行う天気と予測の処理。
- アーカイブの tarball をマシンの外へコピーすること、およびアーカイブの整理。
- TLS と公開ホスト名: アプリは `127.0.0.1` だけで待ち受けます。自前のリバースプロキシを前段に置き、`.env` の `PUBLIC_BASE_URL` をその公開アドレスに、`FORWARDED_HOPS` をプロキシの段数に設定してください。

## トラブルシューティング

- `docker compose --profile standalone ps -a` で `app` と `poller` が動いているか、再起動を繰り返していないかを確認できます。
- `app` や `poller` が再起動を繰り返す、または起動しない場合は、`docker compose --profile standalone logs app`（または `poller`）を読みます。`SESSION_SIGNING_KEY` がない、または既定値のままだと、ここに出ます。
- 取り込みが遅れた場合、ポーラーのログに各回が読む tarball が出ます。事業者のアーカイブ全体を手で取り込むには、`docker compose --profile standalone exec poller python gtfs_pipeline.py ingest /app/archive/<id>/rt --agency-id <id>` を実行します。
