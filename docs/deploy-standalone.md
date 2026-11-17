# 自治体・研究室向けデプロイ手順: 1 台のマシンで自分のフィードを動かす

[English](deploy-standalone.en.md)

Docker が動く 1 台のマシンで、選んだバスのフィードについてアプリ全体が動きます。クラウドのアカウントも、オブジェクトストレージも、LLM のキーも要りません。閲覧者のアカウントも要りません。

## 必要なもの

- Docker（Compose プラグイン付き）と `git`。Linux、macOS、Windows（WSL）のいずれでも構いません。
- フィードと時刻表の URL への外向き HTTPS。外部からマシンへの接続は不要です。
- 事業者ごとに公開 URL を 2 つ: **GTFS-RT TripUpdates** フィードと **GTFS（GTFS-JP）静的 zip**。動作を確認済みのフィードは [`feeds/`](../feeds/README.md) にあります。

## 1. クローンと設定

```bash
git clone https://github.com/TripAndCode/transit-app.git && cd transit-app
cp .env.example .env
```

`.env` はそのままでも動きます。必要なときだけ次の変数を編集します。

| 変数 | いつ |
| --- | --- |
| `DEFAULT_ADMIN_USERNAME`、`DEFAULT_ADMIN_PASSWORD` | 管理者としてサインインするとき（手順 4）。両方のコメントを外します。 |
| `SESSION_SIGNING_KEY` | どちらかのサインイン方式を使うなら必須: `openssl rand -hex 32`。 |
| `POLL_INTERVAL_SEC` | ポーリング間隔の秒数（既定 60）。 |
| `STANDALONE_PORT` | アプリのホスト側ポート（既定 8000、`127.0.0.1` のみ）。 |
| `POSTGRES_PASSWORD`、`CLICKHOUSE_PASSWORD` | Docker ネットワークに他者が届く場合は `transit` から変更します。データベースは作成時のパスワードを保持するため、初回起動の前に設定します。 |

Oracle、R2、天気、LLM の設定は無視して構いません。`GEMINI_API_KEY` と `OPENAI_API_KEY` は空のままにします。その場合、Ask ページのモデルによる回答だけが無効になり、ほかは動きます。

## 2. 起動

```bash
docker compose --profile standalone up -d --build app poller
```

`app poller` を指定すると、スタンドアロン用の構成だけが起動します。専用の Postgres と ClickHouse（ホストのポートなし、専用ボリューム）、スキーマ更新のステップ、アプリ、ポーラーです。`--profile standalone up` だけにしないでください。開発用の 2 つのデータベースまで起動します。初回のビルドには数分かかります。完了したら <http://localhost:8000> を開きます。ページは表示されますが、事業者はまだ登録されていません。

## 3. 事業者を追加する

```bash
docker compose --profile standalone exec app python gtfs_pipeline.py add_agency \
  --feed-url https://example.test/trip_updates.pb --static-url https://example.test/gtfs.zip
```

先に `--dry-run` を付けると、判定だけを表示して何も書き込みません。このコマンドは時刻表をダウンロードし、フィードを 120 秒間（`--probe-seconds`）ポーリングして、フィードの StopTimeUpdate が `(trip_id, stop_sequence)` で時刻表に結合できるかを確認します。URL に `&` を含む場合はシェルで引用符で囲んでください。

出力の末尾を読みます。成功すると `verdict: accept`、事業者名、`agency_id` が表示され、時刻表の読み込みまで済んでいます。それ以外の判定では何も追加されません。

| 判定 | 意味と対処 |
| --- | --- |
| `accept` | StopTimeUpdate の 80% 以上が結合しました。`static_join` で追加されます。 |
| `accept_empty` | `--accept-empty` を付けたときだけ: プローブ中にフィードが何も送りませんでした（運行時間外など）。未検証のまま追加されます。 |
| `name_required` | `agency.txt` に複数の事業者があるか名前がありません。`--name "..."` を付けて再実行します。 |
| `feed_taken` | そのフィード URL はすでに事業者として登録されています。 |
| `url_rejected` | URL が http(s) ではないか、プライベートアドレスを指しています。 |
| `static_unavailable`、`static_unreadable`、`static_incomplete` | zip をダウンロードできない、読めない、または `trips.txt`・`stops.txt`・`stop_times.txt` の行がありません。 |
| `feed_unreachable`、`not_a_feed`、`empty` | フィードが応答しない、GTFS-RT ではない、またはエンティティがありません。運行時間中に再実行します。 |
| `no_trip_updates`、`no_trip_ids`、`no_stop_time_updates` | TripUpdate がない、または `trip_id` や停車時刻がありません。 |
| `stop_id_only` | StopTimeUpdate が停留所を示すだけで `stop_sequence` がなく、結合できません。 |
| `low_join` | 結合したのが 80% 未満です。時刻表とフィードが食い違っています。出力に一致しなかった例が示されます。 |

判定のあとに具体的な理由が出力されます。`--dry-run` を付けた場合は、付けずに再実行すると事業者が追加されます。拒否では何も書き込まれず、ポーラーが再試行することもありません。原因を直してから、もう一度コマンドを実行します。

列を自分で指定したい場合（別の戦略、`trip_id_pattern`、固定の `agency_id`）は、`agencies.csv` と同じ列の CSV を書き、`docker compose --profile standalone cp my_agency.csv app:/tmp/my_agency.csv` でコピーしてから、上の `add_agency` と同じ要領で `seed_agencies /tmp/my_agency.csv` と `refresh-static` を実行します。この方法ではプローブを行いません。

## 4. 管理者としてサインインする

閲覧にサインインは要りません。管理者は `/login` から管理ページ（Agencies ページで戦略を編集できます）に入ります。`.env` に `DEFAULT_ADMIN_USERNAME`、`DEFAULT_ADMIN_PASSWORD`、`SESSION_SIGNING_KEY` を設定し、`docker compose --profile standalone up -d app` を実行します。Google や GitHub を使う方法（`ADMIN_EMAILS` とクライアント ID）は `.env.example` に説明があります。

## 5. データはいつ出るか

ポーラーはサイクルごとに事業者の一覧を読むので、追加から 1 分ほどで拾われ、Pulse には以降のポーリングで得た今日のデータが表示されます。数時間ではなく、最初の数回のポーリングを待ちます。運行時間外のフィードは、最初の便が走るまで表示するものがありません。

終わった日は、次の JST 0 時に履歴になります。UTC の日が閉じたあとは、ポーラーがその日の生のポーリングをまとめ、再取り込みで置き換えます。これは自動で行われます。

進み具合は `docker compose --profile standalone logs -f poller` で確認できます。

## 6. バックアップ

すべては 3 つの名前付きボリュームにあります。`standalone_pgdata`（事業者、時刻表、集計）、`standalone_chdata`（データとしてのポーリング結果）、`standalone_archive`（生のポーリング結果。事業者ごと・日ごとに tarball 1 つ）です。Compose はプロジェクト名（チェックアウトのディレクトリ名）を各ボリュームの前に付けます。

```bash
P=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' $(docker compose --profile standalone ps -q standalone-db))
docker compose --profile standalone stop app poller standalone-clickhouse standalone-db
mkdir -p backup
for v in $(docker volume ls -q --filter label=com.docker.compose.project=$P --filter name=standalone_); do
  docker run --rm -v "$v":/data:ro -v "$PWD/backup":/backup alpine tar czf "/backup/$v.tgz" -C /data .
done
docker compose --profile standalone up -d app poller
```

`P` はこのチェックアウトの Compose プロジェクトなので、ループが触れるのはその 3 つのボリュームだけです。停止中のポーラーは何も記録しません。短い停止は、その長さの欠測になります。`backup/` はマシンの外にコピーしてください。アーカイブのボリュームは削除されず、事業者ごと・日ごとに tarball 1 つずつ増えます。

## 7. アップグレード

```bash
git pull
docker compose --profile standalone up -d --build app poller
```

アプリとポーラーが再起動する前にスキーマ更新のステップ（`gtfs_pipeline.py schema-up`）が走るので、マイグレーションは自動で適用されます。スキーマが変わるリリースの前にはバックアップを取ってください。履歴を整理済みのデータベースで、データを失うマイグレーションがあると、スキーマ更新のステップが拒否します。`up` が失敗を報告し、`app` はそのステップを待つので起動しません。理由は `docker compose --profile standalone logs migrate` で読めます。バックアップを取ったうえで、そのマイグレーションのバージョンを `.env` の `MIGRATE_ALLOW_DESTRUCTIVE_UP` に書き、`up` をもう一度実行します。

## 自動化されていないこと

- 事業者が再公開した時刻表: `docker compose --profile standalone exec app python gtfs_pipeline.py refresh-static` を再実行します。
- Oracle の cron が行う天気と ML 予測の処理。
- tarball やバックアップをマシンの外へアップロードすること、およびアーカイブの整理。
- TLS と公開ホスト名: アプリは `127.0.0.1` だけで待ち受けます。自前のリバースプロキシを前段に置き、それに合わせて `PUBLIC_BASE_URL` と `FORWARDED_HOPS` を設定してください。
- 取り込みが遅れた場合、ポーラーのログに各回が読む tarball が出ます。事業者のアーカイブ全体を手で取り込むには、`docker compose --profile standalone exec poller python gtfs_pipeline.py ingest /app/archive/<id>/rt --agency-id <id>` を実行します。
