# Helm vs Kustomize — AIエージェント適性の比較実験

KubernetesのManifest管理ツールである **Helm** と **Kustomize** について、AIエージェント（Claude）に扱わせた場合どちらが適しているかを定量的に評価する実験リポジトリです。

## 実験の概要

### 背景と目的

AIコーディングエージェントがインフラ作業を補助するユースケースが増える中、「AIエージェントはどちらのツールをうまく扱えるのか」という問いに対して、実験的な根拠を持って答えることを目指しています。

### 評価方法

同一のサンプルアプリケーション（"Bookstore API"）を Helm と Kustomize の両方で構成し、同じ10種類のタスクを Claude に与えて実行結果を比較します。

| 評価軸 | 内容 |
|---|---|
| タスク成功率 | 正しい Manifest が生成されるか |
| トークン効率 | 同じタスクに何トークン必要か |
| ツール呼び出し数 | 何ステップかかるか |
| バリデーション通過率 | `kubectl --dry-run=client` が通るか |

### 実験規模

- タスク数: 10（4段階の複雑度に分類）
- 繰り返し数: 15回/タスク/ツール
- 総実行数: 300回（2ツール × 10タスク × 15回）

---

## サンプルアプリケーション

**Bookstore API** — フロントエンド・API・ワーカーの3層構成

| Kind | リソース名 |
|---|---|
| Deployment | bookstore-frontend, bookstore-api, bookstore-worker |
| Service | bookstore-frontend (LoadBalancer), bookstore-api, bookstore-worker (ClusterIP) |
| HorizontalPodAutoscaler | bookstore-api |
| ConfigMap | bookstore-app-config, bookstore-nginx-conf |
| Secret | bookstore-db-credentials |
| Ingress | bookstore-ingress |

3環境（dev / staging / prod）で以下の値が異なります。

| パラメータ | dev | staging | prod |
|---|---|---|---|
| api replicas | 1 | 2 | 5 |
| api CPU limit | 200m | 500m | 1000m |
| api memory limit | 256Mi | 512Mi | 1Gi |
| image tag | latest | v1.2.0 | v1.2.0 |
| HPA maxReplicas | 3 | 5 | 20 |
| ingress hostname | dev.bookstore.local | staging.bookstore.example.com | bookstore.example.com |

---

## タスク一覧

複雑度の低い順に4段階（Tier）に分類しています。

| ID | Tier | 内容 |
|---|---|---|
| T1-R1 | 1 読み取り | prod 環境の API コンテナのメモリ limit の照会 |
| T1-R2 | 1 読み取り | staging と prod の設定差分の列挙 |
| T2-M1 | 2 単一変更 | staging の API レプリカ数を変更 |
| T2-M2 | 2 単一変更 | prod の全サービスのイメージタグを更新 |
| T2-M3 | 2 単一変更 | dev の API コンテナにのみ環境変数を追加 |
| T3-C1 | 3 クロスカット | ConfigMap を新規作成して全環境の API にマウント |
| T3-C2 | 3 クロスカット | staging/prod の worker に resource requests を追加 |
| T3-C3 | 3 クロスカット | 全環境の HPA 閾値変更 + prod のみ minReplicas を変更 |
| T4-S1 | 4 構造変更 | 新しいマイクロサービス（mailer）を全環境に追加 |
| T4-S2 | 4 構造変更 | `app` ラベルを `app.kubernetes.io/name` に全リソース・全環境でリネーム |

---

## リポジトリ構成

```
ai-manifest-handling/
├── helm/
│   └── bookstore/
│       ├── Chart.yaml
│       ├── values.yaml           # dev（デフォルト）
│       ├── values-staging.yaml
│       ├── values-prod.yaml
│       └── templates/
├── kustomize/
│   └── bookstore/
│       ├── base/                 # 共通定義（dev デフォルト値）
│       └── overlays/
│           ├── dev/
│           ├── staging/
│           └── prod/
├── experiment/
│   ├── config.yaml               # モデル・繰り返し数・タスクリスト
│   ├── tasks/                    # タスク定義 YAML（10件）
│   ├── verifiers/
│   │   ├── lib.py                # 共通検証ライブラリ
│   │   └── verify_task.py        # タスク採点エントリポイント
│   ├── harness/
│   │   ├── agent.py              # Claude SDK エージェントループ
│   │   ├── runner.py             # 実験ループ本体
│   │   ├── reset.py              # git ベースのリセット
│   │   └── parity_check.py      # Helm/Kustomize 同等性チェック
│   └── analysis/
│       └── aggregate.py          # 結果集計・統計
└── results/                      # 実験結果 JSON（gitignore 対象）
```

---

## 実施手順

### 前提条件

以下のツールがインストールされていること。

- Python 3.11 以上
- [uv](https://docs.astral.sh/uv/)
- [Helm](https://helm.sh/docs/intro/install/) v3
- [kubectl](https://kubernetes.io/docs/tasks/tools/) + kustomize（`kubectl kustomize` が動くこと）
- Anthropic API キー

### 1. セットアップ

```bash
git clone https://github.com/hhiroshell/ai-manifest-handling.git
cd ai-manifest-handling

# Python 仮想環境の作成と依存パッケージのインストール
uv venv .venv
uv pip install -r requirements.txt --python .venv/bin/python3

# API キーの設定
export ANTHROPIC_API_KEY=sk-ant-...
```

### 2. parity check（実験前の必須確認）

Helm と Kustomize が全環境で意味的に同一の Manifest を生成することを確認します。実験の公平性を担保する重要なステップです。

```bash
.venv/bin/python3 experiment/harness/parity_check.py
```

`Parity check PASSED` が表示されれば問題ありません。

### 3. 動作確認（1タスクのみ実行）

本番実行の前に、1タスクのみ動かして動作を確認します。

```bash
.venv/bin/python3 experiment/harness/runner.py \
  --task T2-M1 \
  --tool helm \
  --reps 1
```

`results/helm/T2-M1/001.json` に結果が保存されます。

```json
{
  "tool": "helm",
  "task_id": "T2-M1",
  "run": 1,
  "task_success": true,
  "partial_credit": 1.0,
  "llm_calls": 3,
  "input_tokens": 4821,
  "output_tokens": 312,
  "total_tokens": 5133,
  "tool_calls": 5,
  "wall_time_sec": 18.4,
  ...
}
```

### 4. 全実験の実行

```bash
# 全実行（300回、推定所要時間: 2〜10時間）
.venv/bin/python3 experiment/harness/runner.py

# 中断からの再開（完了済みの実行をスキップ）
.venv/bin/python3 experiment/harness/runner.py --resume

# 特定のツール・タスクのみ実行
.venv/bin/python3 experiment/harness/runner.py --tool kustomize --task T3-C1
```

### 5. デバッグモード（トレースの保存と確認）

`--debug` フラグを付けると、各ターンのメッセージ内容とトークン数をまとめたトレースファイルが保存されます。実験後に「どんなやり取りが行われたか」を確認したい場合に使います。

```bash
# --debug を付けると通常の結果 JSON に加えてトレースファイルも保存される
.venv/bin/python3 experiment/harness/runner.py \
  --task T2-M1 --tool helm --reps 1 --debug
```

実行後、以下の2ファイルが生成されます。

```
results/helm/T2-M1/
├── 001.json          # 通常のメトリクス（成功率・トークン数など）
└── 001_trace.json    # ターンごとのやり取りの全記録
```

トレースは `show_trace.py` で確認できます。

```bash
# ターンごとのトークン数サマリーだけ表示
.venv/bin/python3 experiment/harness/show_trace.py \
  results/helm/T2-M1/001_trace.json --summary

# 全ターンの内容を表示（長いテキストは自動省略）
.venv/bin/python3 experiment/harness/show_trace.py \
  results/helm/T2-M1/001_trace.json

# 特定のターンだけ確認
.venv/bin/python3 experiment/harness/show_trace.py \
  results/helm/T2-M1/001_trace.json --turn 3

# 省略なしで全文表示
.venv/bin/python3 experiment/harness/show_trace.py \
  results/helm/T2-M1/001_trace.json --verbose
```

`--summary` の出力例:

```
=== Trace Summary ===
  Tool:    helm
  Task:    T2-M1
  Run:     1
  Turns:   7

Turn   Role         In tokens  Out tokens  Content summary
------------------------------------------------------------------------
   0   user                 -           -  text("You are working in a K...")
   1   assistant        4,821         156  text("I'll update..."), tool_use(read_file)
   2   user                 -           -  tool_result("apiVersion: apps/v1...")
   3   assistant        5,203         312  tool_use(write_file)
   4   user                 -           -  tool_result("Written 843 bytes...")
   5   assistant        5,891          18  text("DONE")
------------------------------------------------------------------------
  Total               15,915         486
```

### 6. 結果の分析

```bash
# サマリー表を標準出力に表示
.venv/bin/python3 experiment/analysis/aggregate.py

# CSV ファイルに保存
.venv/bin/python3 experiment/analysis/aggregate.py --output results/summary.csv
```

出力例:

```
Task       Tool           N  Success      95% CI          Tokens  ToolCalls   WallTime
--------------------------------------------------------------------------------
--- Tier 1 ---
T1-R1      helm          15   93.3%  [68.1%, 99.2%]    2841      3.2       12.1s
T1-R1      kustomize     15   86.7%  [59.5%, 98.3%]    3105      4.1       14.8s
...
```

---

## エージェント設定

全実験で共通の設定を使用します（ツール間の条件を揃えるため）。

| 項目 | 値 |
|---|---|
| モデル | claude-sonnet-4-6 |
| 温度 | 0（再現性確保） |
| 最大ターン数 | 30 |
| 許可ツール | read_file, write_file, list_directory, bash |
| bash 制限 | kubectl / helm / kustomize / cat / diff / git / tree / find / ls / yq |

---

## 採点ルール

各タスクの結果は以下の3段階で評価します。

| レベル | 基準 |
|---|---|
| 2（完全正解） | 全検証チェックが通り、`kubectl --dry-run=client` も通過 |
| 1（部分正解） | 主要チェックは通るが副次的なチェックが失敗（例: 余分なファイルを変更した） |
| 0（不正解） | 主要チェックが失敗、またはYAML構文エラー |

`task_success`（0/1）と `partial_credit`（0.0〜1.0）の両方を記録します。

---

## 統計的考慮

- **検定**: 2標本比率検定（ツール間の成功率比較）
- **多重比較補正**: Bonferroni 補正（α = 0.05 / 10タスク = 0.005）
- **効果量**: Cohen's h（比率）、Cohen's d（トークン数）を併記
- **信頼区間**: Wilson スコア区間（95%）

---

## 実験ハーネスの限界

### Claude Code CLI との差異

このハーネスは Anthropic SDK を直接呼び出すカスタム実装であり、Claude Code CLI（`claude` コマンド）とは異なります。結果を解釈する際には以下の乖離を念頭に置いてください。

| 観点 | Claude Code | このハーネス |
|---|---|---|
| ファイル編集 | `Edit`（差分指定で部分書き換え） | `write_file`（ファイル全体の上書き） |
| ファイル検索 | `Glob`, `Grep` | `bash`（find / cat 相当のみ） |
| ファイル読み込み | `Read`（行番号・範囲指定付き） | `read_file`（全文読み込みのみ） |
| システムプロンプト | Claude Code 専用の詳細なもの | 実験用の簡易なもの |
| コンテキスト管理 | 自動コンパクション | なし |

特に `Edit` ツールの不在が結果に大きく影響します。Claude Code では変更箇所の前後の文字列を指定して部分書き換えができるのに対し、このハーネスではファイル全体を書き直す必要があるため、**トークン消費量が実態より多くなり、意図しない変更が混入するリスクも高くなります**。

### この実験で有効に測れること

- Helm と Kustomize の構造に対するモデルの**理解能力の相対的な差**
- タスク種別（読み取り・単一変更・クロスカット・構造変更）ごとの**難易度の傾向**

### この実験で測れないこと

- Claude Code プロダクトそのものの成功率・トークン効率
- Claude Code 固有のツールセットを使った場合の挙動

### Claude Code CLI を直接使う場合

Claude Code の実際の性能を測定したい場合は、`--print` モードによる非インタラクティブ実行が有効です。ただし詳細なメトリクス（ターンごとのトークン数など）を取得するには、Claude Code のフック機能やコストレポートを別途活用する必要があります。

```bash
claude -p "タスクの内容..." --output-format json --max-turns 30
```

---

## ライセンス

MIT
