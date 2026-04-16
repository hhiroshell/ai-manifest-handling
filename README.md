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

### 5. 結果の分析

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

## ライセンス

MIT
