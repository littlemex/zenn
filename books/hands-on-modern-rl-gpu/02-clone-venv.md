---
title: "Chapter 2: clone と章ごとの venv セットアップ"
---


接続が完了している前提 ([Chapter 1: 環境への接続手順](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/01-setup))。本書では以下を扱います:

1. GPU 環境の動作確認
2. `walkinglabs/hands-on-modern-rl` を EFS 上に clone
3. 章ごとの venv 作成と依存パッケージインストール
4. 動作確認のためのスモークテスト実行

すべての作業は **`coder` ユーザー** で行います (`/home/coder` が EFS 上に紐付いているため、再作成しても消えない)。

## このドキュメントで使うコマンドのお作法

- **ローカル Mac** からの実行: `bash scripts/run-tasks.sh ...` のように Task Runner を使う。SSM Run Command 経由でリモートに送信される
- **EC2 上 (code-server / SSM Session)** での実行: `source /home/coder/venvs/.../bin/activate` のように直接シェルで叩く

各セクションで **「ローカル」/「EC2 上」** を明示します。

---

## 共通: ローカルで使う変数

ローカル Mac のシェルで以下を 1 度設定しておくと、以降のコマンドが楽になります。

```bash
export AWS_PROFILE=claude-code
export AWS_REGION=ap-northeast-1

INSTANCE_ID=$(aws cloudformation describe-stacks --region ap-northeast-1 \
  --stack-name rl-gpu-ws \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
  --output text)
echo "InstanceId: $INSTANCE_ID"
```

---

## 1. GPU 環境の動作確認

### 方法 A: Task Runner で一括チェック (ローカルから実行)

```bash
cd /path/to/investigations/hands-on-modern-rl-gpu/gpu-cdk

bash scripts/run-tasks.sh \
  -i $INSTANCE_ID \
  -r ap-northeast-1 \
  -f tasks-gpu/00-verify-gpu.json \
  --state-file /tmp/task-state-verify-gpu.json \
  --clean-state
```

#### 引数の重要ポイント

- **`-f` (task-file)**: タスク JSON ファイル指定。`-t` ではない (run-tasks.sh の help 参照)
- **`--state-file`**: state 管理ファイルを **タスクごとに分ける**。デフォルトは `/tmp/task-state-<instance-id>.json` で、code-server-setup の状態と衝突する
- **`--clean-state`**: 既存 state をクリアして 1 から実行

`tasks-gpu/00-verify-gpu.json` は以下 4 タスクを実行します:

1. `00-nvidia-smi`: GPU 検出
2. `01-cuda-version`: nvcc / CUDA 版数
3. `02-pytorch-import`: `/opt/pytorch` venv で `torch.cuda.is_available()` / bf16 サポート確認
4. `03-disk-space`: ディスク・メモリ・インスタンス情報

### 方法 B: 手動で確認 (EC2 上)

SSM session で接続:

```bash
# ローカルから
aws ssm start-session --target $INSTANCE_ID --region ap-northeast-1
```

EC2 上で:

```bash
# coder ユーザーに切り替え
sudo -i -u coder

# nvidia-smi
nvidia-smi

# DLAMI 付属の PyTorch venv で確認
source /opt/pytorch/bin/activate
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

期待される出力:

```
2.11.0+cu130 True NVIDIA RTX PRO 6000 Blackwell Server Edition
```

DLAMI が「g7e.2xlarge は非対応」と warning を出しますが、実際の動作には影響しません (Description 未更新のため)。

---

## 2. リポジトリの clone

### 方法 A: Task Runner で自動 (推奨、ローカルから)

```bash
cd /path/to/investigations/hands-on-modern-rl-gpu/gpu-cdk

bash scripts/run-tasks.sh \
  -i $INSTANCE_ID \
  -r ap-northeast-1 \
  -f tasks-gpu/01-clone-modern-rl.json \
  --state-file /tmp/task-state-clone.json \
  --clean-state
```

このタスクは:

1. `00-base-pkgs`: apt で `git`, `swig`, `cmake`, `libgl1`, `libosmesa6-dev`, `ffmpeg`, `python3-venv`, `python3-pip`, `python3-dev` 等をインストール
2. `01-clone`: `https://github.com/walkinglabs/hands-on-modern-rl.git` を `/home/coder/work/hands-on-modern-rl/` に clone (idempotent)
3. `02-venv-root`: 章ごと venv 用の親ディレクトリ `/home/coder/venvs/` を作成

#### つまづきポイント: python3-venv

Ubuntu 24.04 のシステム Python 3.12 はデフォルトで `python3-venv` が入っていません。`apt install python3.12-venv` をしないと `python3 -m venv` した結果に `bin/activate` が作られず、後続の venv 作成タスクが
`bash: ...activate: No such file or directory` で失敗します。`tasks-gpu/01-clone-modern-rl.json` の base packages にはこの予防策が入っています。

### 方法 B: 手動 (EC2 上)

```bash
# まずローカルから SSM session
aws ssm start-session --target $INSTANCE_ID --region ap-northeast-1

# EC2 上で apt install (sudo は ssm-user 経由で sudo 可能)
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git build-essential swig cmake libgl1 libglib2.0-0 \
  libosmesa6-dev patchelf ffmpeg unzip \
  python3-venv python3-pip python3-dev

# coder に切り替え
sudo -i -u coder

# clone
mkdir -p /home/coder/work
cd /home/coder/work
git clone https://github.com/walkinglabs/hands-on-modern-rl.git

# venv 親ディレクトリ
mkdir -p /home/coder/venvs

ls /home/coder/work/hands-on-modern-rl/code/
```

期待される出力:

```
appendix_common_pitfalls   chapter04_dqn               chapter08_rlhf
chapter01_cartpole         chapter05_policy_gradient   chapter09_alignment
chapter02_dpo              chapter06_actor_critic      chapter09_continuous_control
chapter03_mdp              chapter07_ppo               chapter09_grpo_rlvr
                           chapter10_agentic_rl        chapter11_vlm_rl
                           chapter12_future_trends
```

---

## 3. 章ごとの venv 作成と依存インストール

`requirements.txt` のバージョン制約が古典 RL 系と LLM 系で衝突するため、**章単位で独立した venv** を作ります。

### 方法 A: Task Runner で自動 (推奨、ローカルから)

```bash
bash scripts/run-tasks.sh \
  -i $INSTANCE_ID \
  -r ap-northeast-1 \
  -f tasks-gpu/02-install-chapter-deps.json \
  --state-file /tmp/task-state-deps-ch01.json \
  --clean-state \
  -v '{"CHAPTER":"chapter01_cartpole"}'
```

#### 引数の重要ポイント

- **`-v '{"KEY":"VAL"}'`**: 変数上書きは **JSON 形式 1 つの文字列** として指定 (`--var` ではない)
- **`-s TASK_ID` (`--start-from`)**: 失敗したタスクから resume したい場合に使用 (例: `-s 02-install-requirements`)
- **state-file は章ごと別に**: 異なる章のセットアップを並行 / 順次回す場合、state-file 名を変えておくこと

このタスクは:

1. `00-precheck`: 章ディレクトリ存在確認
2. `01-create-venv`: `python3 -m venv --system-site-packages /home/coder/venvs/$CHAPTER`
   - `--system-site-packages` で DLAMI 付属の torch wheel を再利用 → 高速 (CUDA 部の再 download なし)
3. `02-install-requirements`: `pip install -r requirements.txt`
4. `03-smoke-test`: `import torch, gymnasium, ...` で sanity check

### 全章を一括セットアップしたい場合 (ローカルから)

ループでまとめて流す:

```bash
CHAPTERS=(
  chapter01_cartpole
  chapter02_dpo
  chapter03_mdp
  chapter04_dqn
  chapter05_policy_gradient
  chapter06_actor_critic
  chapter07_ppo
  chapter08_rlhf
  chapter09_alignment
  chapter09_continuous_control
  chapter09_grpo_rlvr
  chapter10_agentic_rl
  chapter11_vlm_rl
  chapter12_future_trends
)

for ch in "${CHAPTERS[@]}"; do
  echo "==> $ch"
  bash scripts/run-tasks.sh \
    -i $INSTANCE_ID -r ap-northeast-1 \
    -f tasks-gpu/02-install-chapter-deps.json \
    --state-file /tmp/task-state-deps-${ch}.json \
    --clean-state \
    -v "{\"CHAPTER\":\"${ch}\"}" || echo "FAILED: $ch"
done
```

ディスク使用量目安: 全 14 章 venv 合計で **約 10-15 GB** (gymnasium / transformers などを章ごとに持つため)。EFS なので余裕。

### 方法 B: 手動 (EC2 上)

```bash
sudo -i -u coder

CHAPTER=chapter01_cartpole

# venv 作成
python3 -m venv --system-site-packages /home/coder/venvs/$CHAPTER
source /home/coder/venvs/$CHAPTER/bin/activate

# 依存インストール
pip install --upgrade pip
pip install -r /home/coder/work/hands-on-modern-rl/code/$CHAPTER/requirements.txt

# 動作確認
python -c "import torch, gymnasium; print(torch.__version__, gymnasium.__version__)"
```

---

## 4. スモークテスト

### 方法 A: Task Runner で実行 (ローカルから)

```bash
bash scripts/run-tasks.sh \
  -i $INSTANCE_ID \
  -r ap-northeast-1 \
  -f tasks-gpu/03-run-chapter-script.json \
  --state-file /tmp/task-state-run-ch01.json \
  --clean-state \
  -v '{"CHAPTER":"chapter01_cartpole","SCRIPT":"1-ppo_cartpole.py"}'
```

ログは EC2 上の `/home/coder/runs/chapter01_cartpole/<timestamp>-1-ppo_cartpole.py.log` に保存されます。

### 方法 B: 手動 (EC2 上)

```bash
sudo -i -u coder
source /home/coder/venvs/chapter01_cartpole/bin/activate

cd /home/coder/work/hands-on-modern-rl/code/chapter01_cartpole
python 1-ppo_cartpole.py
```

CartPole は CPU でも 30 秒程度で完走します (GPU は使われない)。

### その他の章

| 章 | venv | スクリプト例 | 所要時間目安 |
|---|---|---|---|
| 1 | chapter01_cartpole | 1-ppo_cartpole.py | 30 秒 |
| 4 | chapter04_dqn | dqn_cartpole.py | 1-2 分 |
| 7 | chapter07_ppo | ppo_lunar_lander.py | 5-10 分 |
| 8 | chapter08_rlhf | sft_pipeline.py | 10-30 分 (初回モデル DL あり) |
| 9b | chapter09_grpo_rlvr | grpo_math_reasoning.py | 30 分以上 |
| 11 | chapter11_vlm_rl | geometry_counting_dataset.py | データ生成のみ短時間 |

---

## 5. Task Runner の場所と仕組み

`gpu-cdk/tasks-gpu/` 配下に GPU 用 JSON タスク定義があります:

| ファイル | 内容 |
|---|---|
| `00-verify-gpu.json` | GPU/CUDA/PyTorch の動作確認 |
| `01-clone-modern-rl.json` | apt 基礎パッケージインストールと git clone |
| `02-install-chapter-deps.json` | 章別 venv 作成 + `pip install -r requirements.txt` |
| `03-run-chapter-script.json` | 任意の章スクリプトを venv 上で起動し、`/home/coder/runs/` にログ保存 |

実行は `gpu-cdk/scripts/run-tasks.sh` 経由:

```bash
bash scripts/run-tasks.sh -i $INSTANCE_ID -r ap-northeast-1 \
  -f tasks-gpu/<JSON> \
  --state-file /tmp/task-state-<unique>.json \
  --clean-state \
  -v '{"KEY":"VAL"}'
```

詳細は `tasks-gpu/README.md` も参照してください。

### 重要: state-file の管理

`run-tasks.sh` はデフォルトで `/tmp/task-state-<instance-id>.json` を使います。複数の異なるタスクを順に実行する場合、**前回の state が残っているとタスク ID 重複でスキップ判定が誤動作** することがあります。`--state-file` でユニークな名前を渡すか、`--clean-state` で毎回クリアしてください。

---

## 6. EFS 永続化のメリット

- インスタンスを停止/起動しても `/home/coder` の中身が消えない
- 訓練済みモデル、Hugging Face cache、wandb / SwanLab のログ全部 EFS 上に置ける
- インスタンスタイプ変更で EC2 が CFN replace されても、新インスタンスから同じ `/home/coder` にアクセス可能 (deploy.sh が自動で再 mount する)

代わりに、**書き込み速度は EBS / NVMe より遅い** (NFS 越しのため)。大量の小ファイル I/O が必要な場合は `/mnt/local` (NVMe 1.7TB) を使うこと:

```bash
# Hugging Face datasets を NVMe にキャッシュ
export HF_DATASETS_CACHE=/mnt/local/hf-datasets-cache
mkdir -p $HF_DATASETS_CACHE
```

ただし `/mnt/local` はインスタンス再作成で消える点に注意。永続的な訓練成果物は EFS に置いてください。

---

## トラブルシューティング

### `bin/activate: No such file or directory`

`python3-venv` が未インストール (または `python3.12-venv`)。`tasks-gpu/01-clone-modern-rl.json` の `00-base-pkgs` を流すか、手動で:

```bash
sudo apt-get install -y python3-venv python3.12-venv python3-dev
```

### Task Runner が「タスク n を完了済み」とスキップする

別の `--state-file` を指定するか、`--clean-state` を追加。

```bash
bash scripts/run-tasks.sh ... --state-file /tmp/task-state-NEW.json --clean-state
```

### `pip install` が遅い / タイムアウトする

- `--system-site-packages` を必ず付けて DLAMI の torch wheel を継承
- 章 8/11 などモデルダウンロードを伴う章は時間がかかる (10-30 分)

### `gymnasium[atari]` で ROM が無い

```bash
pip install autorom[accept-rom-license]
AutoROM --accept-license
```

Pokémon Red ROM はリポジトリに含まれていない (Chapter 4)。自分で用意する必要あり。

### `nvidia-smi` が反応しない

- カーネルアップデート後にドライバが外れることがある:
  ```bash
  sudo /opt/aws/dlami/bin/setup-script.sh
  ```
- それでも復旧しない場合はインスタンス再起動

### `swanlab` のローカルダッシュボード (Chapter 1, 4 で使用)

```bash
# EC2 上
swanlab login --host http://127.0.0.1:5092
```

ローカル PC からブラウザで見るにはポート転送:

```bash
bash scripts/deploy.sh --port-forward --stack-name rl-gpu-ws \
  -p 5092:5092 -r ap-northeast-1
```

---

## 次のステップ

各章のチュートリアルは 本書の各 Chapter 配下を参照してください。

- 古典 RL 入門なら **第 1 章 → 第 3 章 → 第 4 章 → 第 7 章**
- LLM 系から入るなら **第 2 章 → 第 8 章 → [Chapter 14: GRPO/RLVR](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/14-grpo-rlvr)**
- VLM やエージェントに興味があるなら **第 10 章 / [Chapter 16: VLM RL](https://zenn.dev/littlemex/books/hands-on-modern-rl-gpu/viewer/16-vlm-rl)**
