# s380-relay (Python 版)

リーダ間で ISO14443 スマートカードの通信を、ネットワーク越しに **APDU レイヤ**で
中継する Python 製ツールです。[`nfcpy`](https://nfcpy.readthedocs.io) の上に実装。
Rust 版 [`s380-relay`](../S380-relay) の移植で、**同じ改行区切り JSON のワイヤ
プロトコル**を話すため、Python サーバ・Python クライアント・Rust バイナリ・Android
HCE クライアントを自由に組み合わせられます。

```
 端末 ⇢ (Type A / ISO-DEP) ⇢ [client: RC-S380] ──APDU を TCP で──▶ [server: RC-S380 / PC/SC] ⇢ 実カード
        └ ここで ISO-DEP を終端                                      └ ここで ISO-DEP を終端
                                    リンクを渡るのは ISO 7816-4 APDU のみ
```

一方（**サーバ**）に実カードを載せ、もう一方（**クライアント**）が端末/スマホに対して
合成した Type4（NFC-A）カードとして振る舞います。端末が送るコマンド APDU が TCP で
サーバへ中継され、実カードへ送られ、応答 APDU が返されます。端末からは実カードと
会話しているように見えます。実カードは **Type A でも Type B でも**よく（どちらも
ISO14443-4 に収束し、リンクを渡るのは APDU だけ）。

**自分が所有する（またはテスト許可のある）リーダ・カード**で、研究・CTF・相互接続検証に
使うことを想定しています。所有していない、または許可の無いカードの中継は違法になり
得ます。許可を得る責任は利用者にあります。

## なぜ nfcpy か

このアイデアは利用者の発案です — *「RC-S380 なら nfcpy で行ける。エミュレーションは
できないけどコマンドのやり取りはできる」*。nfcpy は RC-S380（NFC Port-100）を両方の
役割で駆動できます:

- **リーダ**（`sense` / `exchange`）… カード側、およびクライアントの端末フロントエンド
- **カードエミュレーション**（Type A の Type4 ターゲットとして `listen`、RATS に応答）
  … クライアント/スマホ側

RC-S380 はカード側で **Type A も Type B も**駆動でき、Type B カードの ISO-DEP
データフェーズも最後まで運べます。nfcpy には **RC-S300（Port-400）ドライバが無い**
ものの、Type B に PC/SC リーダは不要です。カード側に `pyscard` 経由で任意の
**PC/SC** リーダ（Sony 純正ドライバの RC-S300 / PaSoRi 4.0 等）を使うこともできますが、
RC-S380 1 台で十分です。

## 必要なもの

- Python 3.8 以降
- `nfcpy>=1.0`（`pip install nfcpy`）
- PC/SC カード側バックエンド（任意）: `pyscard`（`pip install "s380-relay[pcsc]"`）
- 各面に RC-S380 を 1 台（1 ホストに 2 台、または 2 ホストに 1 台ずつ）
- リーダへの USB アクセス権。**Windows** では nfcpy が `libusb` 経由で RC-S380 と
  通信するため、[Zadig](https://zadig.akeo.ie/) でデバイスを **WinUSB** ドライバに
  紐づける必要があります（Options → List All Devices → `SONY RC-S380`（USB ID
  `054C:06C1`）を選択 → **WinUSB** をインストール）。Sony 純正ドライバを置き換えます。
  戻すには Device Manager で WinUSB ドライバを削除してください。

## インストール

```sh
pip install -e .            # このディレクトリから
pip install -e ".[pcsc]"    # PC/SC カード側バックエンド込み
pip install -e ".[dev]"     # pytest 込み
```

インストールせずに `python -m s380_relay ...`（このディレクトリから）でも動きます。

## 使い方

サブコマンド `server` / `client` / `list`。ログは `-v`（info）/ `-vv`（debug）、
または環境変数 `S380_LOG` で制御。

### リーダ一覧

```sh
$ s380-relay list
attached RC-S380 readers:
  index 0  bus 001 addr 014  pid 0x06C1
  index 1  bus 001 addr 015  pid 0x06C1
```

1 ホストに 2 台のときは各面に別々の `--device-index` を（サーバ既定 `0`、
クライアント既定 `1`）。2 ホストなら既定のままで OK。

### サーバ（カード側）

実カードをこのリーダに置いてから:

```sh
s380-relay -v server --listen 0.0.0.0:7878
```

| フラグ | 既定 | 意味 |
|------|---------|---------|
| `-l, --listen <addr:port>` | `127.0.0.1:7878` | TCP 待受アドレス |
| `--tech <auto\|a\|b>` | `auto` | 実カードの ISO14443 方式（`auto` は Type A → Type B の順に検出） |
| `--reader <port100\|pcsc>` | `port100` | カード側バックエンド |
| `--pcsc-name <部分文字列>` | (Sony) | PC/SC リーダ選択用の部分文字列 |
| `-d, --device-index <n>` | `0` | どの RC-S380（port100。`list` 参照） |
| `-t, --timeout <ms>` | `1000` | コマンド毎のタイムアウト |

> **Type B も RC-S380 で動きます。** カード側は `--reader port100` で Type A・
> Type B の両方を中継でき、RC-S380 が Type B のデータフェーズ（ISO-DEP の
> I/R/S ブロック、WTX、チェイニング）も最後まで運びます（マイナンバーカードで
> 動作確認済み）。PC/SC リーダ（`--reader pcsc`、RC-S300 / PaSoRi 4.0 等）は
> 必須ではなく、任意の代替手段です。

### クライアント（スマホ側）

サーバに接続して Type4 カードをエミュレート。端末/スマホをかざす:

```sh
s380-relay -v client --connect <server-ip>:7878
```

| フラグ | 既定 | 意味 |
|------|---------|---------|
| `-c, --connect <addr:port>` | `127.0.0.1:7878` | サーバアドレス |
| `-d, --device-index <n>` | `1` | どの RC-S380（`list` 参照） |
| `--no-wtx` | (off) | 中継前に S(WTX) を送らない |
| `--wtxm <1-59>` | `10` | 待ち時間延長の乗数 |
| `-t, --timeout <ms>` | `1000` | コマンド毎のタイムアウト |
| `-w, --window <seconds>` | `1.0` | listen のウィンドウ長 |

中継の往復がスマホのフレーム待ち時間を超えないよう、各コマンドの前に `S(WTX)` を
スマホへ送ります。

## ワイヤプロトコル

TCP 上の改行区切り JSON — Rust 版・Android 版と完全に同一:

```jsonc
// client → server
{"type":"get_card"}
{"type":"apdu","data":"00A4040007A0000002471001","timeout_ms":1000}
// server → client
{"type":"card","tech":"B","info":"5090be4e5b000005e0b381a100"}
{"type":"apdu","data":"6F..9000"}
{"type":"error","message":"..."}
```

`data` は全て 16 進エンコードした ISO 7816-4 APDU。ISO-DEP のフレーミング（PCB、CRC、
チェイニング、WTX）は各面で処理され、リンクを渡りません。

## 診断用サンプル

このディレクトリから実行（パッケージを import します）:

| サンプル | 内容 |
|---------|------|
| `python examples/relay_test_client.py [host:port]` | サーバに接続し数個の APDU を送る（リーダ不要） |
| `python examples/find_aid.py [host:port]` | 候補 AID を SELECT して実カードを特定 |
| `python examples/terminal.py [index]` | RC-S380 を ISO-DEP リーダとして駆動し、エミュレート側をかざして全体を試験 |
| `python examples/probe_typeb.py [index] [nocid\|senseonly]` | RC-S380 の Type B 診断: SENSB_RES を検出し、任意で ATTRIB + I ブロック1個 |

## 動作の仕組み

1. クライアントが `get_card` でカードを要求。サーバは実カードを ISO-DEP に activate
   （Type A は `sense` + `RATS`、Type B は `SENSB` + `ATTRIB`、または PC/SC の
   `connect`）し、ATS/ATQB/ATR を報告。
2. クライアントは nfcpy の `listen` で合成 Type4（NFC-A）カードを提示（RATS は nfcpy が
   応答）。スマホがかざすと最初のコマンドブロックが返り、クライアントが**コマンド
   APDU**を再構成。
3. コマンド APDU を TCP で中継。サーバは PCD 側 ISO-DEP ステートマシン（`isodep.Pcd`）で
   実カードと交換（ブロック番号トグル、コマンド/応答チェイニング、S(WTX)）し、応答
   APDU を返す。
4. クライアントは応答を ISO-DEP I ブロックに包んでスマホへ。2 つの ISO-DEP セッションは
   独立で、共有されるのは APDU のみ。

## 注意・制限

- 中継できるのは ISO14443-4 カードのみ（ただの Type 2 タグには APDU レイヤが無い）。
- エミュレートするカードの UID/ATS は合成で、実カードのものではありません。
- Type B も（Type A と同様）RC-S380 で直接中継できます。PC/SC リーダは任意です。
- 各面 1 台のため、サーバは一度に 1 クライアントのみ対応。

## テスト

```sh
pytest
```

プロトコルのコーデック、ISO-DEP ステートマシン、サーバのリクエスト処理（偽カード使用）を
カバー。ハードウェア不要です。

## ライセンス

Apache-2.0 — `LICENSE` を参照。
