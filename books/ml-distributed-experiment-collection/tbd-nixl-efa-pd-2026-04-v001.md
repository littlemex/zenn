---
title: "NIXL over EFA による真の P/D 分離推論 Part 5.1 環境構築編"
emoji: "🛠"
type: "tech"
topics: ["NIXL", "vLLM", "EFA", "Kubernetes", "AWS"]
published: false
---

## はじめに

本記事の価値を先に示す。UCX_TLS を 1 行切り替えるだけで EFA SRD と TCP の両経路を同じ手続きで測定できる基盤を、SageMaker HyperPod EKS 上で vLLM の NixlConnector を動かす形で構築する手順書である。Part 5.1 は環境構築編と位置付け、測定結果自体は別記事に委ねる。プロジェクト実体は `nixl-efa-pd-2026-04/phase2-g5-pd-benchmark/` にある。想定読者は vLLM / Kubernetes 経験のある基盤エンジニアで、NIXL / UCX / EFA に覚えはあるが実装経験は薄い層である。

本文で使う略語を先に補足する。NIXL は vLLM の KV cache を Pod 間で転送する抽象レイヤである。UCX （Unified Communication X）は NIXL の下回りのネットワーク抽象ライブラリである。SRD は Scalable Reliable Datagram の略で、EFA （Elastic Fabric Adapter）独自のトランスポートである。DNS-1035 は RFC 1035 準拠のラベル規約、envsubst は `${VAR}` をシェル環境変数で置換する GNU gettext ユーティリティ、hostPath はノード上のパスを Pod にマウントするボリューム種別である。

環境構築の勘所は再現性と冪等性の 2 軸にある。再現性は設定と手順を一箇所に封じ込め誰が走らせても同じ結果を出せること、冪等性は何度繰り返しても最終状態が収束することである。本プロジェクトは「3 層のテンプレート」と「前後検証フック」でこの 2 軸を支える。

## 全体像とスコープ

本節では測定対象のハードウェアと経路命名 R3 / R4 の意味を先に示し、その後に vLLM / NIXL 側の初期化パラメータを列挙する。

測定対象は vLLM の Prefill Pod と Decode Pod を別ノードに配置する真の P/D 分離推論である。Prefill 側は g5.12xlarge （NVIDIA A10G 4 枚、EFA 1 本）、Decode 側は g5.16xlarge （A10G 1 枚、EFA 1 本）をそれぞれ 1 台使う。AWS 公式仕様では g5.16xlarge は EFA 非対応とされているが、本実験の SageMaker HyperPod EKS 環境では実測で `vpc.amazonaws.com/efa=1` が露出していた。HyperPod 側の特殊構成に起因する振る舞いと考えられる。パブリック EC2 の g5.16xlarge では EFA を使えないため、再現時はこの差分に注意されたい。

モデルは Qwen/Qwen2.5-7B-Instruct、max_model_len は 8192、GPU メモリ使用率 0.85 をデフォルトとする。vLLM 側 KVTransferConfig には `NixlConnector` を指定し `kv_buffer_device=cpu`、backends は `["UCX"]` とする。A10G / g5 が GPUDirect RDMA 非対応で EFA 利用時もホスト DRAM 経由のステージングを要するためで、cuda にすると NIXL 初期化時に `ibv_reg_mr(access=0x1) failed` で起動しない。

経路切替は UCX_TLS で完結する。`srd,self,sm` なら SRD 経由で EFA、`tcp,self,sm` なら TCP フォールバックに落ちる。前者を R3、後者を R4 と呼ぶ （シリーズ通しの R1 から R6 までの命名規則に沿うが、本記事が扱うのは R3 と R4 のみである）。切替は UCX_TLS / UCX_NET_DEVICES / FI_PROVIDER を一括で動かす 3-axis flip と、UCX_TLS のみ動かす single-axis flip の双方を提供する。論文的 A/B には single-axis、運用に近い切替観察には 3-axis が適する。

## 再現性と冪等性を支える 3 層のテンプレート

`phase2-g5-pd-benchmark/` では 3 層のテンプレートを設計した。最上位が Run ID を担う variant YAML、中段が envsubst で具象化される Kubernetes manifest、最下段が汎用 JSON 実行エンジン向けの Task 定義である。

### 第 1 層 variant YAML

最上位は variant YAML で、`setup/variants/r3_efa_srd.yaml` / `r4_tcp.yaml` が該当しファイル名自体が Run ID になる。`env:` ブロック配下に UCX_TLS / UCX_NET_DEVICES / FI_PROVIDER / FI_EFA_USE_DEVICE_RDMA / KV_BUFFER_DEVICE / UCX_TLS_SINGLE_AXIS / PREFIX_CACHE / ENFORCE_EAGER / EFA_LIMIT_LINE など差分の出うる軸を列挙する。`runner.sh load_variant` は jq / yq に依存せず awk で 1 階層の key-value のみを拾う実装で、ネストや flow style は読めないが bash 単独で動く利点を優先した。抜粋を以下に示す。

```yaml
# setup/variants/r3_efa_srd.yaml の抜粋
id: r3_efa_srd
description: "R3 baseline (EFA SRD via UCX). g5 + A10G + Qwen2.5-7B + kv_buffer_device=cpu"
env:
  BACKEND: efa
  UCX_TLS: "srd,self,sm"
  UCX_NET_DEVICES: auto
  FI_PROVIDER: efa
  FI_EFA_USE_DEVICE_RDMA: "0"
  KV_BUFFER_DEVICE: cpu
  UCX_TLS_SINGLE_AXIS: "0"
```

### 第 2 層 manifest テンプレート

次の層は Kubernetes manifest テンプレートで、`k8s-manifests/pd/` 配下に `prefill.yaml.tmpl` / `decode.yaml.tmpl` / `proxy.yaml.tmpl` / `bench.yaml.tmpl` / `efa-counter-reader-ds.yaml.tmpl` / `hf-cache-pvc.yaml` を置き envsubst で展開する。`pd_deploy.sh` の `render()` は以下の 22 個を `envsubst` にリテラル指定する。Pod spec に偶然現れる `$` 記号 （Bash command substitution 等）の誤展開を避けるための限定列挙である。

```text
RUN_ID / DNS_RUN_ID / NAMESPACE /
PREFILL_NODE / DECODE_NODE / PROXY_NODE /
VLLM_IMAGE / MODEL_NAME / MAX_MODEL_LEN / GPU_UTIL / HF_TOKEN /
UCX_TLS / UCX_NET_DEVICES / FI_EFA_USE_DEVICE_RDMA / FI_PROVIDER /
EFA_LIMIT_LINE / HOSTPATH_BASE / KV_BUFFER_DEVICE /
UCX_LOG_LEVEL / NIXL_LOG_LEVEL / PREFIX_CACHE_FLAG / EAGER_FLAG
```

### 第 3 層 Task JSON

最下層は JSON の Task 定義で、`setup/tasks/pd/` 配下に `deploy_pd.json` / `teardown_pd.json` / `capture_efa_counters.json` / `capture_efa_counters_host.json` / `capture_pod_placement.json` / `capture_gpu_util.json` / `sweep_with_redeploy.json` / `iperf3_pd_aligned*.json` / `deploy_efa_counter_reader.json` などを置き `setup/task_runner.sh` が汎用 JSON 実行エンジンとして解釈する。`deploy_efa_counter_reader.json` は後述の host 側 EFA カウンタ DaemonSet を投入するタスクで、再現手順でも単独で呼ぶ。`type` で `apply` / `exec` / `sweep` / `validate` / `capture` / `analyze` / `shell` を切り替えられ、`${VAR}` 展開と `$$VAR` エスケープを標準サポート、`--env KEY=VALUE` で上書きできる。タスク追加は新しい .json を 1 枚置くだけで閉じる。

## 冪等性を保証する runner.sh と redeploy-sweep

3 層を繋ぐのが `setup/runner.sh` で、dispatcher の `case` には `run / preflight / postflight / deploy / teardown / sweep / redeploy-sweep / analyze / status / clean` の 10 個の subcommand が列挙してある。`teardown` は独立 subcommand で異常終了後の手動クリーンアップに使う。代表的な呼び出しは以下である。

```bash
./setup/runner.sh redeploy-sweep --variant r3_efa_srd --iterations 5 --seed 42
```

各 iteration 内部は down → up → capture_pod_pre → capture_host_pre → placement → sweep → capture_pod_post → capture_host_post → down の 9 段階である。EFA counter capture を pod 視点と host 視点に分けるため 9 段階になる。冒頭の `down || true` で前 iteration の失敗状態を持ち越さず、delete 対象不在の非零終了を許容して前に進む。

`sweep` 単発は既デプロイ Pod での短サイクル試験向け、`redeploy-sweep` は毎 iteration 建て直しで placement 局所最適化を排除する向きで、Phase 2 は後者を標準とした。Pod Ready 待機は prefill 900 秒 / decode 900 秒 / proxy 300 秒に分け、重い prefill / decode と軽量な proxy の待ち時間を切り分けている。

`--seed 42` と `--iterations 5` の組み合わせは統計的に 3 点を担保する。まず `--iterations 5` で分散評価に要るサンプル数を確保する。`redeploy-sweep` は iteration ごとに Pod を建て直すので placement 局所最適や CPU / NUMA ピニングの偶然性を排除し、独立サンプルに近付く。さらに `--seed 42` で乱数列を固定して再実行時に同じトラフィック系列を再生できる。

## preflight と postflight による前後検証

`setup/validators/` には preflight 7 本 （`preflight_v01_cluster.sh` から `preflight_v07_images.sh`）と postflight 5 本 （`postflight_p01_ucx_transport.sh` から `postflight_p05_pod_restarts.sh`）が置かれ、`runner.sh preflight` が辞書順に実行してどれか一つでも非零終了すれば全体も非零で返す。

preflight の責務を以下にまとめる。ContainerCreating からの進行停止を事前に潰せる。

| ID | 検査対象 | 失敗時の想定着地点 |
| --- | --- | --- |
| V-01 | kubectl の cluster-info 疎通 | kubeconfig と AWS_PROFILE の整合を確認 |
| V-02 | EFA device plugin の存在 | `kube-system/dependencies-aws-efa-k8s-device-plugin` を確認、HyperPod 側の更新に追随 |
| V-03 | NVIDIA device plugin の存在 | `dependencies-nvidia-device-plugin` DaemonSet を確認 |
| V-04 | prefill / decode / proxy node の Ready （2 node 構成では proxy=decode） | `kubectl get nodes` で NotReady 原因確認、env.local.sh のノード名を修正 |
| V-05 | host 視点での `/sys/class/infiniband` 可視性 （Pod 内視点ではない） | EFA driver load を DaemonSet pod で ls し、ノード kernel module の load を確認 |
| V-06 | HF_TOKEN が環境変数もしくは Secret で設定 | `env.local.sh` の書き換え再 source、または `kubectl create secret generic` で登録 |
| V-07 | コンテナイメージの pull 可能性 | `VLLM_IMAGE` のタグずれ、imagePullSecret 不備を確認 |

postflight は測定後に経路妥当性を検証する。単体失敗は警告として残し、サイレントな挙動差異を拾う。

| ID | 検査対象 | 失敗時の想定着地点 |
| --- | --- | --- |
| P-01 | UCX ログの transport signature （`rdmap`/`srd` (R3) もしくは `tcp` (R4)） | UCX_TLS_OVERRIDE と UCX_TLS_SINGLE_AXIS を照合 |
| P-02 | EFA counter delta と variant の整合 （R3 increment、R4 0 増分） | pre / post capture JSON を突き合わせ |
| P-03 | concurrency × seed × iteration 分の結果 JSON 完備 | sweep 途中落ちを runner ログから特定 |
| P-04 | `nvidia-smi` CSV と bench 実行時間帯の時系列重なり | capture_gpu_util の間隔と bench 開始時刻を確認 |
| P-05 | Pod の restartCount が 0 のままか | crashloop の有無を `kubectl describe pod` で確認 |

### preflight 失敗時の復旧要点

迷いやすい 3 項目 （V-02 / V-06 / V-07） の着地点を掘り下げる。V-02 が落ちた場合は EFA device plugin の DaemonSet が未インストールか Pending かに分かれる。`kubectl -n kube-system get ds | grep efa` で実名を確認し、必要なら HyperPod 運用側に補修を依頼する。V-06 が落ちた場合は `HF_TOKEN` 未設定か該当 secret 不在を順に確認する。gated モデルを使う場合は Hugging Face 側のアクセス許諾も要件になる。V-07 が落ちた場合は `VLLM_IMAGE` タグずれ、imagePullSecret 不備、エグレス制限などが原因で、`kubectl run` で手動 pull を試すと NetworkPolicy や rate limit を切り分けられる。

## env.local.sh と設定の秘匿

設定値は 3 種類に分けている。全員共通のデフォルトは `setup/env.example.sh`、個人差の出る値と秘密情報 （HF_TOKEN 等）は `setup/env.local.sh` に退避する。`env.local.sh` は `.gitignore` の `**/env.local.sh` / `**/.env` で除外してあり誤って commit されない作りにした。雛形の抜粋を示す。

```bash
export CLUSTER_NAME="${CLUSTER_NAME:-CHANGE-ME-eks-cluster-name}"
export PREFILL_NODE="${PREFILL_NODE:-CHANGE-ME-prefill-node-hostname}"
export DECODE_NODE="${DECODE_NODE:-CHANGE-ME-decode-node-hostname}"
export HF_TOKEN="${HF_TOKEN:-}"
export AWS_PROFILE="${AWS_PROFILE:-default}"
```

ワークフローは `cp setup/env.example.sh setup/env.local.sh` で雛形を複製し、必須 5 変数 （AWS_PROFILE / CLUSTER_NAME / PREFILL_NODE / DECODE_NODE / HF_TOKEN）を自環境値に書き換えて `source setup/env.local.sh` するだけである。他の変数 （NAMESPACE / HOSTPATH_BASE / MODEL_NAME 等）はデフォルトで運用できる。`__nixl_check_required` が未設定変数と CHANGE-ME を検出して警告する。

AWS_PROFILE には注意が要る。`runner.sh` の内部デフォルトは `claude-code` だが著者の検証環境名で公開資材には使えない。読者は `env.local.sh` で自環境の profile 名に上書きすること。HF_TOKEN は過去に測定結果 JSON に混入した事例があり、コミット前に `grep -r hf_` を走らせる運用を推奨する。露出したら Hugging Face 側から即座に無効化する。

## UCX_TLS 切替と 3-axis / single-axis

本節では切替の 2 系統と、single-axis と 3-axis の分岐条件を整理する。

pd_deploy.sh には BACKEND による一括 flip と UCX_TLS_OVERRIDE による部分 flip の 2 系統がある。`BACKEND=efa` は UCX_TLS を `srd,self,sm`、UCX_NET_DEVICES を `auto`、FI_PROVIDER を `efa`、FI_EFA_USE_DEVICE_RDMA を `0`、EFA_LIMIT_LINE を `vpc.amazonaws.com/efa: "1"` に設定する。`BACKEND=tcp` では UCX_TLS が `tcp,self,sm`、UCX_NET_DEVICES が `all`、FI_PROVIDER が `tcp` に変わり EFA_LIMIT_LINE は空コメントに置き換わる。`FI_EFA_USE_DEVICE_RDMA="0"` は両 case で 0 固定で、TCP 側でも 0 に落として libfabric EFA プロバイダが裏で RDMA を発火する可能性を抑えている。

UCX_TLS_OVERRIDE 指定時の分岐条件を以下に整理した。srd 系 override で 3-axis を強制すると EFA 側設定を TCP 向けに動かしてしまい SRD が選ばれない逆転現象が起きるため、この分岐で踏み外しを防ぐ。

| UCX_TLS_SINGLE_AXIS | override に srd 含む？ | 動作 |
| --- | --- | --- |
| 1 | 任意 | UCX_TLS のみ差し替え （pure single-axis flip） |
| 0 | 含まない （tcp 系） | 3-axis flip、UCX_TLS / UCX_NET_DEVICES / FI_PROVIDER を同時に動かす |
| 0 | 含む （`srd,self,sm` 直指定） | flip せず UCX_TLS だけ反映、EFA 側設定は BACKEND 既定のまま |

## Manifest 層での重要な工夫 3 点

本節では DNS-1035 命名制約、proxy pod の起動順序問題、proxy pod のノード固定という 3 つの運用上のハマりを反映した工夫を順に示す。

### DNS-1035 compliance 対応

`r3_efa_srd` のアンダースコアは Pod / Service 名の DNS-1035 制約に抵触するため、`pd_deploy.sh` 冒頭で `DNS_RUN_ID="${RUN_ID//_/-}"` とハイフンに置換し、Pod / Service 名には `DNS_RUN_ID`、結果ディレクトリや RESULT_TAG には `RUN_ID` を使い分ける。入れ忘れると `is not a valid DNS-1035 label` で apply 自体が通らない。

### Proxy pod の race condition 回避

vLLM の `disagg_proxy_demo.py` は起動時に `verify_model_config` で prefill / decode の `/v1/models` を同期的に叩くが、モデルロード中に 404 を返す window があり proxy がぶつかると即落ちする。`restartPolicy: Never` のため Error 固定で手で消さない限り復帰しない。対策として proxy 起動スクリプトに 200 返却待ち curl ループを入れ、demo が `127.0.0.1` に bind する箇所を sed で `0.0.0.0` に書き換えてから exec する。readinessProbe は `/health` 未実装のため FastAPI 自動生成の `/openapi.json` で代用した。

以下に示す curl ループは 180 回 （5 秒 × 180 ≒ 900 秒）待って駄目なら非零終了する。900s のエラー文言はこの値に由来する。

```bash
for endpoint in "$P_IP:8100" "$D_IP:8200"; do
  n=0
  until curl -fsS "http://${endpoint}/v1/models" >/dev/null 2>&1; do
    if [ "$n" -ge 180 ]; then
      echo "[ERROR] ${endpoint}/v1/models did not become ready within 900s" >&2
      exit 1
    fi
    n=$((n+1)); sleep 5
  done
done
```

このループが失敗すると proxy pod は Error 固定で復帰しない。`restartPolicy: Never` のため再起動もされないため、pd_deploy のタイムアウトまで握り込んで iteration 全体が失敗する。

Ready 確認後、disagg_proxy_demo.py の bind アドレスを `127.0.0.1` から `0.0.0.0` に書き換えてから exec に入る。sed による置換本体は以下である。

```bash
sed -i 's|uvicorn.Config(app, port=self.port, loop="uvloop")|uvicorn.Config(app, host="0.0.0.0", port=self.port, loop="uvloop")|' \
  /vllm-workspace/examples/online_serving/disaggregated_serving/disagg_proxy_demo.py
```

### Proxy pod の node 固定

`proxy.yaml.tmpl` の nodeSelector で `kubernetes.io/hostname: ${PROXY_NODE}` を指定し、2 node 構成では `PROXY_NODE=${DECODE_NODE}` で decode と同居、3 node 構成では独立ノードを当てる。iteration 間で proxy の位置が揺らぐ latency ジッタを排除する目的で、Phase 2 初期に proxy がたまたま別ノードに飛んで latency が数 ms 跳ねる事例を確認している。

## HyperPod EKS 特有の気づき

SageMaker HyperPod EKS 固有の挙動が 3 点ある。

### EFA interface が 1 本しか露出されない

g5 ノードの EFA device plugin はインスタンスあたり 1 本しか露出せず、g5.12xlarge の仕様上は 4 本持てるものの HyperPod では 1 本に絞られる。`vpc.amazonaws.com/efa: "1"` を要求する Pod しか立ち上がらない前提になり、multi-rail 想定のコードはそのままでは動かない。

### 管理ノードの不可視性

HyperPod 管理ノードは HyperPod サービスアカウント所有で、参加者アカウントから `aws ec2 describe-instances` で直接見えない。動作検証は原則 Pod 内から `kubectl exec` で行うため、ssh 前提の手順書はそのまま通らない。

### device plugin 名の追跡

device plugin の状態は preflight の V-02 / V-03 で `kube-system/dependencies-aws-efa-k8s-device-plugin` と `dependencies-nvidia-device-plugin` を確認する。HyperPod 管理コンポーネントは更新で名前が変わりうるため、preflight で壁打ちすると気付ける。複数ユーザー共有時は `NAMESPACE=nixl-pd-bench` に加え `HOSTPATH_BASE=/tmp/nixl-pd-bench-$USER` でユーザー分離を推奨する。

## Host 側の EFA カウンタを読む DaemonSet

EFA の稼働証拠は `/sys/class/infiniband/*/ports/1/hw_counters/` のカウンタで取るのが自然だが、g5 HyperPod では Pod 内の `/sys/class/infiniband` に EFA の hw_counters が現れない。EFA ドライバが host kernel module として動作し、Pod 側 rdma subsystem view に露出しない仕様に起因する。preflight V-05 が確認しているのはこの host 視点でのカウンタ露出であり、Pod 内 view ではない点には注意する。

対策として `efa-counter-reader` DaemonSet を導入した。`k8s-manifests/pd/efa-counter-reader-ds.yaml.tmpl` で定義し、prefill / decode ノードに 1 Pod ずつ配置、ホスト `/sys` を readOnly hostPath で `/host/sys` にマウントし busybox を `sleep infinity` で常駐させる。securityContext は privileged=false のまま `runAsUser: 0` で読み取り、`readOnlyRootFilesystem: true` / `allowPrivilegeEscalation: false` で最小権限化した。hostNetwork も不要、リソース要求は cpu 10m / memory 32Mi である。

カウンタ取得は `setup/tasks/pd/capture_efa_counters_host.json` が担当し、redeploy-sweep の各 iteration の pre / post で 2 回呼ばれる。reader Pod への `kubectl exec` で hw_counters を読み取り JSON 化し、`analyzers/efa_counter_diff.py` が rdma_read_bytes / rdma_write_bytes の増分をテーブル化する。R3 で increment / R4 で 0 が揃えば、UCX_TLS が srd なのに実体 TCP に落ちていた偽経路問題を事後的に弾ける。

## 再現手順

前提ツールは aws cli v2、jq、envsubst、python3.10 以上、kubectl である。envsubst は GNU gettext パッケージに同梱される （Debian / Ubuntu は `sudo apt-get install gettext-base`、macOS は `brew install gettext`）。kubectl は最低バージョン要件なし。コンテナイメージは `VLLM_IMAGE=vllm/vllm-openai:latest` を指定し、NIXL / UCX / vLLM の具体バージョンはイメージ側に内包される。`aws eks update-kubeconfig` は `runner.sh` が自動で叩くため kubeconfig コンテキストが違っていても切り替わる。

実行手順は以下である。

```bash
cd /path/to/nixl-efa-pd-2026-04/phase2-g5-pd-benchmark
cp setup/env.example.sh setup/env.local.sh
vi setup/env.local.sh         # AWS_PROFILE / CLUSTER_NAME / PREFILL_NODE / DECODE_NODE / HF_TOKEN
source setup/env.local.sh
./setup/runner.sh preflight   # V-01 から V-07 まで [OK] を確認
./setup/runner.sh deploy --variant r3_efa_srd
./setup/runner.sh run setup/tasks/pd/deploy_efa_counter_reader.json
./setup/runner.sh redeploy-sweep --variant r3_efa_srd --iterations 5 --seed 42
./setup/runner.sh postflight --variant r3_efa_srd
./setup/runner.sh analyze --run-id r3_efa_srd
```

deploy では 3 Pod が envsubst 展開後に `kubectl apply` され、prefill / decode は 900 秒、proxy は 300 秒の timeout で Ready 待ちに入る。`deploy` 単発は redeploy-sweep 前段のスモークテストで、一度立ち上がることを確認してから本測定に進む。counter reader DaemonSet は 1 回置けば使い回せる。`deploy_efa_counter_reader.json` は第 3 層 Task JSON の 1 つで host 側カウンタ読取 DaemonSet を投入する。redeploy-sweep は再実行しても前回結果を上書きせず別ディレクトリに書き出す。後片付けは `./setup/runner.sh teardown --variant r3_efa_srd`、状態確認は `status`、キャッシュ整理は `clean` である。

## ハマりどころ索引

実際に踏んだハマりを索引にまとめる。参照節名は実在の見出しと一致させてある。

| 症状 | 原因 | 対処 | 参照節 |
| --- | --- | --- | --- |
| apply が DNS-1035 エラー | RUN_ID のアンダースコアが Pod 名に入っている | DNS_RUN_ID を Pod 名に使う | Manifest 層での重要な工夫 3 点 |
| proxy が Error 固定 | /v1/models 404 window と verify_model_config の衝突 | proxy 起動時に curl 200 待ちループを挟む | Manifest 層での重要な工夫 3 点 |
| bench pod に exec 不可 | 完走後 Completed に遷移 | redeploy-sweep で iteration ごとに再 apply | 冪等性を保証する runner.sh と redeploy-sweep |
| CWD が期待と違う | nohup バックグラウンドでの相対 source | 絶対パスで cd と source | env.local.sh と設定の秘匿 |
| hw_counters が見えない | EFA ドライバが host kernel module で Pod view 非露出 | efa-counter-reader DaemonSet で host /sys を mount | Host 側の EFA カウンタを読む DaemonSet |
| UCX_TLS が srd なのに実体 TCP | BACKEND flip と UCX_TLS_OVERRIDE 併用時の 3-axis 不整合 | host 視点の hw_counters 差分でポストフライト検証 | Host 側の EFA カウンタを読む DaemonSet |

## クラスター変数の例示

クラスター変数の設定例を以下に示す。著者環境依存の値は各自で書き換える。項目名は `env.local.sh` の環境変数名で統一してある。

| 環境変数名 | 値 （著者環境） | 備考 |
| --- | --- | --- |
| CLUSTER_NAME | sagemaker-eks-cluster-`<cluster-id>` | 各自置き換え |
| REGION | us-west-2 | HyperPod 配置リージョン |
| PREFILL_NODE | hyperpod-i-XXXX... | A10G 4 枚、EFA 1 本 |
| DECODE_NODE | hyperpod-i-YYYY... | A10G 1 枚、EFA 1 本 |
| PROXY_NODE | decode と同居 | 2 node 構成時 |
| NAMESPACE | nixl-pd-bench | 専用 |
| HOSTPATH_BASE | /tmp/nixl-pd-bench-$USER | 共有クラスター推奨 |
| MODEL_NAME | Qwen/Qwen2.5-7B-Instruct | デフォルト |
| KV_BUFFER_DEVICE | cpu | A10G では必須 |

## まとめ

本記事では NIXL over EFA による真の P/D 分離推論を再現可能に検証する環境構築を整理した。設計の柱は、3 層のテンプレートで Run ID を変えるだけで経路切替が閉じる構造、`runner.sh redeploy-sweep` による冪等な tear-down / set-up ループ、preflight 7 本と postflight 5 本の両面検証の 3 点である。

適用時の要点は 5 つある。第 1 に、必須 5 変数 （AWS_PROFILE / CLUSTER_NAME / PREFILL_NODE / DECODE_NODE / HF_TOKEN） の書き換えを確実に行うこと。第 2 に、preflight から deploy の順序を厳守し ContainerCreating 停止を事前に潰すこと。第 3 に、A10G 系では `KV_BUFFER_DEVICE=cpu` を外さないこと。第 4 に、EFA 経路検証時は `efa-counter-reader` DaemonSet を並行配置して host 視点のカウンタ差分を取ること。第 5 に、proxy pod を nodeSelector で固定し iteration 間の placement ジッタを抑えることである。次回は本環境上の性能測定結果を扱う。参考資料は [NIXL 本体](https://github.com/ai-dynamo/nixl)、[UCX](https://openucx.readthedocs.io/)、[SageMaker HyperPod EKS](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-eks.html) を併読するとよい。

