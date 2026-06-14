# HEMS Echonet Lite Integration for Home Assistant

[![HACS](https://img.shields.io/badge/hacs-default-blue)](https://hacs.xyz/)
[![Quality Scale](https://img.shields.io/github/manifest-json/quality_scale/sayurin/hems_echonet_lite?filename=custom_components/echonet_lite/manifest.json&label=quality+scale&color=mediumpurple)](https://www.home-assistant.io/docs/quality_scale/)
[![License](https://img.shields.io/github/license/sayurin/hems_echonet_lite)](https://github.com/sayurin/hems_echonet_lite/blob/master/LICENSE)
[![Version](https://img.shields.io/github/v/release/sayurin/hems_echonet_lite)](https://github.com/sayurin/hems_echonet_lite/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/sayurin/hems_echonet_lite/latest/total)](https://github.com/sayurin/hems_echonet_lite/releases/latest)
[![Starts](https://img.shields.io/github/stars/sayurin/hems_echonet_lite?style=flat&color=gold)](https://github.com/sayurin/hems_echonet_lite)
[![Sponsor](https://img.shields.io/github/sponsors/sayurin?color=darksalmon)](https://github.com/sponsors/sayurin)

[English](README.md)

[pyhems](https://github.com/sayurin/pyhems) を使用した Home Assistant 向け ECHONET Lite インテグレーションです。UDP マルチキャストを介してローカルネットワーク上の ECHONET Lite 対応機器と通信します。クラウド不要、アカウント不要、API キー不要です。

## ユースケース

- エアコンをリモートまたはオートメーションで制御し、状態を監視する
- 太陽光発電システムの発電量や蓄電池の充電量を監視する
- 電力の安い深夜に沸き上げるようにエコキュートをスケジュール制御する
- 時刻や在宅状態に応じて電気錠を施錠・開錠する
- 日射量やスケジュールに応じて電動ブラインドやシャッターを開閉する
- 環境条件やスケジュールに応じて照明を制御する

## 対応機器

デバイスクラスは 2 種類に分類されます。

- **安定版** — 実機で動作確認済み。デフォルトで有効、追加設定不要。
- **実験的** — 実機での検証未実施。インテグレーションオプションで有効化が必要。

### 安定版デバイスクラス

| クラスコード | 機器 | HA プラットフォーム |
|------------|------|------------------|
| 0x0130 | 家庭用エアコン | Climate + 汎用エンティティ |
| 0x0135 | 空気清浄機 | Fan + 汎用エンティティ |
| 0x026B | 電気温水器（エコキュート） | Water Heater + 汎用エンティティ |
| 0x026F | 電気錠 | Lock + 汎用エンティティ |
| 0x0279 | 住宅用太陽光発電 | 汎用エンティティ |
| 0x027D | 蓄電池 | 汎用エンティティ |
| 0x05FD | スイッチ（JEM-A/HA端子対応） | 汎用エンティティ |
| 0x05FF | コントローラ | 汎用エンティティ |

動作確認済みハードウェア:
- **家庭用エアコン**: 三菱電機 霧ヶ峰 Z シリーズ
- **空気清浄機**: シャープ KI-SX70-W
- **電気錠**: 大和電器 エコーネットライトアダプタ
- **太陽光発電 / 蓄電池**: シャープ SUNVISTA
- **スイッチ**: パナソニック HF-JA1
- **コントローラ**: シャープ JH-RVB1、JH-RWL8

### 実験的デバイスクラス

以下のクラスを使用するには、インテグレーションオプションで **「実験的なデバイスクラスを有効にする」** をオンにする必要があります。

**専用 HA プラットフォームエンティティあり:**

| クラスコード | 機器 | HA プラットフォーム |
|------------|------|------------------|
| 0x0133 | 換気扇 | Fan + 汎用エンティティ |
| 0x0134 | エアコン換気扇 | Fan + 汎用エンティティ |
| 0x0260 | 電動ブラインド | Cover + 汎用エンティティ |
| 0x0263 | 電動シャッター | Cover + 汎用エンティティ |
| 0x0290 | 一般照明 | Light + 汎用エンティティ |
| 0x0291 | 単機能照明 | Light + 汎用エンティティ |

**汎用エンティティのみ**（センサー、電力メーター、EV 充電器、調理機器など 50 以上のクラス — インテグレーションオプション UI で一覧を確認できます）。

## 対応機能

### Climate — 家庭用エアコン（0x0130）

- **運転モード**: オフ、自動、冷房、暖房、除湿、送風
- **風速**: 自動、弱（レベル 1）〜強（レベル 8）、9 段階
- **風向**: オフ、上下、左右、上下左右
- **温度**: 0〜50°C、1°C 刻み

### Fan — 空気清浄機 / 換気扇（0x0133、0x0134、0x0135）

- **風速**: 8 段階をパーセンテージにマッピング
- **プリセットモード**: 自動、手動

### Water Heater — 電気温水器（0x026B）

運転状態（EPC 0x80）、運転モード（EPC 0xB0）、目標温度（EPC 0xB3）を 1 つのエンティティに集約します。

- **運転モード**: `auto`（自動沸き上げ）、`manual`（手動沸き上げ）、`manual_off`（手動停止 / 外出）、`off`
- **目標温度**: EPC 0xB3 によるセット（機器の MRA 定義から導出した範囲、1°C 刻み）
- **現在温度**: 実測湯温（EPC 0xC1）— 単独センサーエンティティとしても公開

### Lock — 電気錠（0x026F）

- **施錠状態**: メイン錠（EPC 0xE0）とサブ錠（EPC 0xE1、広告されている場合）の両方が施錠されている場合に「施錠」と判定
- **ジャム状態**: アラームステータス（EPC 0xE5）が異常を報告している場合に表示
- **施錠 / 解錠**: メイン錠（EPC 0xE0）への書き込みのみ

### Cover — 電動ブラインド（0x0260）/ 電動シャッター（0x0263）

- **コマンド**: 開く、閉じる、停止（常に利用可能）
- **開度制御**（EPC 0xE1）: 機器がセット操作に対応している場合に利用可能、0〜100%
- **チルト制御**（EPC 0xE2）: 機器がセット操作に対応している場合に利用可能、0〜100%（0〜180° にマッピング）
- **状態**（EPC 0xEA）: 開、閉、開中、閉中、停止。広告されていない場合は開度パーセンテージにフォールバック

### Light — 照明（0x0290、0x0291、0x02A3、0x02A4）

| 機能 | 0x0290 一般 | 0x0291 単機能 | 0x02A3 システム | 0x02A4 拡張 |
|------|:-:|:-:|:-:|:-:|
| オン / オフ | ✓ | ✓ | ✓ | ✓ |
| 明るさ（EPC 0xB0、0〜100%） | ✓ | ✓ | ✓ | ✓ |
| 色温度（EPC 0xB1） | ✓ | — | — | — |
| 照明モードエフェクト（EPC 0xB6） | ✓ | — | — | — |

**色温度プリセット**（0x0290 のみ）: 電球色（2700 K）、白色（4000 K）、昼白色（5000 K）、昼光色（6500 K）。任意のケルビン値は最も近いプリセットに丸められます。

**照明モードエフェクト**（0x0290 のみ）: `auto`、`normal`（主照明）、`night`（常夜灯）、`color`（カラー照明）。

### 汎用エンティティプラットフォーム

その他すべてのプロパティは、ECHONET Lite プロパティ定義に基づいて自動マッピングされます。

| 条件 | 書き込み可能 | 読み取り専用 |
|------|-----------|------------|
| 2 値 enum | Switch | Binary Sensor |
| 3 値以上 enum | Select | Sensor（enum） |
| 1 値 enum | Button | — |
| 数値 | Number | Sensor |

## メーカー独自拡張機能

ECHONET Lite 仕様の範囲を超えて、特定のメーカー向けにプロパティ定義が追加されています。機器のメーカーコードが一致した場合に自動的に適用されます。

### シャープ — 空気清浄機（0x0135）

シャープの空気清浄機は、メーカー独自の EPC 0xF1 を通じて追加の環境データを公開します。シャープの機器が検出されると、以下のセンサーエンティティが追加されます。

| エンティティ | 単位 | 説明 |
|------------|------|------|
| Temperature（温度） | °C | 本体内部で計測した室温 |
| Humidity（湿度） | %RH | 本体内部で計測した相対湿度 |
| PM2.5 | µg/m³ | 粒子状物質濃度 |

### シャープ — 住宅用太陽光発電（0x0279）

シャープの太陽光発電システムは、メーカー独自の EPC を通じてストリング単位の入力データを公開します。シャープの機器が検出されると、以下のセンサーエンティティが追加されます。

| エンティティ | 単位 | EPC |
|------------|------|-----|
| 入力電圧 1〜4 | V | 0xF2 |
| 入力電流 1〜4 | A | 0xF3 |
| 入力電力 1〜4 | W | 0xF4 |

### シャープ — コントローラ（0x05FF）

シャープのホームエネルギーコントローラは、メーカー独自の EPC を通じて系統の売買電データを公開します。シャープの機器が検出されると、以下のセンサーエンティティが追加されます。

| エンティティ | 単位 | EPC |
|------------|------|-----|
| 瞬時売電電力 | W | 0xF2 |
| 瞬時買電電力 | W | 0xF3 |
| 積算売電電力量 | Wh | 0xF4 |
| 積算買電電力量 | Wh | 0xF5 |

## データ更新

インテグレーションはポーリングとイベント駆動更新の両方を使用します。

- **イベント駆動**: プロパティ変化通知（INF フレーム）に対応した機器は、受信直後に状態を即時反映します。
- **プロパティポーリング**: 通知を送信しない機器は 60 秒ごとにポーリングします。
- **機器の再探索**: 1 時間ごとにマルチキャストで再探索するため、新たに参加した機器も自動検出されます。
- **ヘルスモニタリング**: 5 分間 ECHONET Lite フレームを受信しない場合、修復イシューを作成します。

## インストール

### HACS（推奨）

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=sayurin&repository=hems_echonet_lite&category=integration)

1. 上のボタンを選択するか、HACS で **「HEMS Echonet Lite」** を検索します。
2. インテグレーションをインストールします。
3. Home Assistant を再起動します。

### 手動インストール

1. `custom_components/echonet_lite` を Home Assistant の `custom_components` ディレクトリにコピーします。
2. Home Assistant を再起動します。

## 前提条件

- Home Assistant 2026.3 以降
- Home Assistant と同一ローカルネットワーク上に ECHONET Lite 対応機器が存在すること
- ネットワーク上で UDP マルチキャストトラフィック（アドレス 224.0.23.0、ポート 3610）が許可されていること
- コンテナや VM で Home Assistant を実行している場合は、マルチキャストトラフィックが適切に転送されていること（例: Docker の `network_mode: host`）

## 設定

1. **設定 → デバイスとサービス → インテグレーションを追加** を開きます。
2. **「HEMS Echonet Lite」** を検索します。
3. ネットワークインターフェースを選択します。
   - **自動**（`0.0.0.0`）: すべてのインターフェースでリッスン（推奨）
   - **特定の IP**: 特定のネットワークインターフェースにバインド
4. インテグレーションが ECHONET Lite マルチキャストグループ（224.0.23.0:3610）でのリッスンを開始し、機器を自動検出します。

> Home Assistant インストールごとに 1 つのインスタンスのみ許可されます。

### オプション

セットアップ後、**設定 → デバイスとサービス → HEMS Echonet Lite → 設定** から変更できます。

| オプション | 説明 | デフォルト |
|----------|------|----------|
| 実験的なデバイスクラスを有効にする | 実機での検証が行われていないデバイスクラスも登録します。正しく動作しない場合があります。 | オフ |

### 再設定

ネットワークインターフェースはいつでも **設定 → デバイスとサービス → HEMS Echonet Lite → 再設定** から変更できます。

## 使用例

### スケジュールによるエアコン制御

外出時にエアコンを自動でオフにし、帰宅前に再起動します。

```yaml
automation:
  - alias: "外出時にエアコンをオフ"
    triggers:
      - trigger: state
        entity_id: group.everyone
        to: not_home
    actions:
      - action: climate.turn_off
        target:
          entity_id: climate.living_room_air_conditioner

  - alias: "帰宅前に冷房を開始"
    triggers:
      - trigger: state
        entity_id: group.everyone
        to: home
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.living_room_air_conditioner
        data:
          hvac_mode: cool
          temperature: 26
```

### 深夜の割安電力でエコキュートを沸き上げ

電気料金が安い深夜帯に電気温水器を沸き上げるようスケジュール設定します。

```yaml
automation:
  - alias: "深夜に沸き上げ開始"
    triggers:
      - trigger: time
        at: "23:00:00"
    actions:
      - action: water_heater.set_operation_mode
        target:
          entity_id: water_heater.electric_water_heater
        data:
          operation_mode: auto

  - alias: "朝に沸き上げ停止"
    triggers:
      - trigger: time
        at: "06:00:00"
    actions:
      - action: water_heater.set_operation_mode
        target:
          entity_id: water_heater.electric_water_heater
        data:
          operation_mode: manual_off
```

### 就寝時の自動施錠

毎晩決まった時間に電気錠を施錠し、解錠状態だった場合は通知を送ります。

```yaml
automation:
  - alias: "就寝時に自動施錠"
    triggers:
      - trigger: time
        at: "23:30:00"
    conditions:
      - condition: state
        entity_id: lock.front_door
        state: unlocked
    actions:
      - action: lock.lock
        target:
          entity_id: lock.front_door
      - action: notify.mobile_app
        data:
          message: "玄関が施錠されていなかったため、自動的に施錠しました。"
```

### 日没時にブラインドを閉じる

日没に合わせて電動ブラインドを自動で閉じます。

```yaml
automation:
  - alias: "日没時にブラインドを閉じる"
    triggers:
      - trigger: sun
        event: sunset
        offset: "-00:30:00"
    actions:
      - action: cover.close_cover
        target:
          entity_id: cover.living_room_blind
```

## 既知の制限事項

- IPv4 ネットワークのみ対応しています。
- ネットワーク上で UDP マルチキャストが有効になっている必要があります。
- 機器がプロパティマップでアドバタイズしていないプロパティは利用できない場合があります。
- 実験的デバイスクラスは実機での検証を行っていないため、正しく動作しない場合があります。
- インストールごとに 1 つのインテグレーションインスタンスのみサポートしています。

## トラブルシューティング

### 機器が検出されない

インテグレーション設定後、機器がまったく表示されない場合。

1. ECHONET Lite 対応機器の電源が入っており、ネットワークに接続されていることを確認します。
2. UDP マルチキャストトラフィック（224.0.23.0:3610）がネットワーク上で許可されていることを確認します。
3. Docker を使用している場合は、コンテナが `network_mode: host` を使用しているか、マルチキャストルーティングが正しく設定されていることを確認します。
4. インテグレーション設定で **自動** の代わりに特定のネットワークインターフェースを選択してみてください。
5. `echonet_lite` または `pyhems` に関するエラーメッセージがないか、Home Assistant ログを確認します。

### 一部の機器が検出されない

一部の ECHONET Lite 機器は表示されるが、他の機器が表示されない場合。

1. 見つからない機器が実験的デバイスクラスの場合、インテグレーションオプションで **「実験的なデバイスクラスを有効にする」** を有効にします。
2. 一部の機器は応答に時間がかかることがあります。再探索は 1 時間ごとに実行されるため、数分待ってから再確認してください。
3. **設定 → デバイスとサービス → HEMS Echonet Lite → ⋮ → 再読み込み** からインテグレーションを再読み込みしてみてください。
4. 機器が ECHONET Lite に対応しているか確認してください。一部の家電はデフォルトで ECHONET Lite が無効になっており、メーカーのアプリや設定から有効化が必要な場合があります。

### 機器が「使用不可」と表示される

機器は検出されたが、後に「使用不可」と表示される場合。

1. 機器のネットワーク接続と電源状態を確認します。
2. 機器がネットワーク通信を無効にする省電力モードに入っていないか確認します。
3. **設定 → システム → 修復** でインテグレーションが報告している問題がないか確認し、提示された解決手順に従ってください。

### 修復イシュー

インテグレーションは以下の状況で修復イシューを自動作成します。

- **ランタイム非活性**: 5 分間 ECHONET Lite フレームを受信していない — 機器の電源とネットワーク接続を確認してください。
- **ランタイムクライアントエラー**: ネットワークエラーが発生した — 修復フローを使用してサービスを再起動してください。

## インテグレーションの削除

インテグレーションを削除するには、**設定 → デバイスとサービス** を開き、**HEMS Echonet Lite** を選択して **削除** を選びます。

### 個別機器の削除

個々の機器は **設定 → デバイスとサービス → HEMS Echonet Lite → （機器） → 削除** から削除できますが、機器がローカルネットワーク上でアクティブでない場合のみ（インテグレーションから到達不能な場合のみ）削除できます。

機器がまだ検出されている場合（電源が入っており応答している場合）、削除は拒否されます。先に機器の電源をオフにするか、ネットワークから切り離してから再試行してください。

## 謝辞

- [ECHONET コンソーシアム](https://echonet.jp/) — ECHONET Lite 仕様の策定
- [pyhems](https://github.com/sayurin/pyhems) — ECHONET Lite プロトコル処理ライブラリ
