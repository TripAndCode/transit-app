# フィードディレクトリ

[English](README.en.md)

`japan.csv` は GTFS-RT フィードと GTFS 静的 zip の一覧です。新しくデプロイする人が一覧から事業者を選べます（[デプロイ手順](../docs/deploy-standalone.md)）。これが元データで、`agencies.csv` は `agency_id` を持つ行の写しです。両者が食い違うと `tests/unit/test_feeds_catalogue.py` が失敗します。`scripts/bus_kyo_association_feeds.py` は協会の行をここから読みます。

列: `name_ja`、`prefecture`（事業者の本社所在地の県。協会の一覧には書かれていないため手で補っており、誤りがあれば直してください）、`agency_id`（`agencies.csv` で使う ID。ほかは空）、`realtime_url`（TripUpdates。重複不可）、`static_url`、`ingest_strategy` と `static_strategy`（`seed_agencies` で読み込める行だけ。空は「`add_agency` のプローブに任せる」）、`licence`、`platform`、`source`（行の出どころ）、`verdict`、`verdict_date`、`probe_seconds`、`verdict_reason`、`coverage_refuted`（プローブが否定した任意の RT フィールド）、`note`。

`verdict` は最後の `add_agency --dry-run` の結果（`pipeline.feed_probe.Verdict` の値）で、日付、ポーリングした長さ、プローブが出力した理由が付きます。その日時点の記録で、あとからフィードが変わることがあります。空は未プローブです。各判定の意味は[手順](../docs/deploy-standalone.md)にあります。

## フィードを追加する

1. 公開元から realtime と static の URL を取り、`source` に出どころを書きます。
2. 開発用ではない使い捨てのデータベースで、運行時間中にプローブします（空のフィードは `empty` になります。スタンドアロン構成の専用データベースでも構いません）。手順のスタックを起動した状態で:
   `docker compose --profile standalone exec app python gtfs_pipeline.py add_agency --dry-run --feed-url '<realtime>' --static-url '<static>'`
   ポーリングの長さは既定値です。
3. 判定、日付（`YYYY-MM-DD`）、ポーリングの長さ、出力された理由を付けて行を追加します。拒否された場合も理由は出力のまま引用します。
4. `pytest tests/unit/test_feeds_catalogue.py` が列の形式、フィード URL の重複、`agencies.csv` との一致を確認します。

## ライセンス

- `bus-kyo.or.jp` の行は広島県バス協会の一覧（<https://www.bus-kyo.or.jp/gtfs-open-data>）に由来し、同ページはクリエイティブ・コモンズ CC0 で提供と明記しています。同ページには、予告なくフィードが中断・中止される場合があること、それに関して各バス事業者とシステム会社は責任を負わないことも書かれています。
- `licence` が空の行は、リポジトリに記録がありません。利用する前に公開元の利用条件を確認してください。
- 負荷をかけないでください。ポーラーの既定は 1 フィードにつき 1 分に 1 回です。
