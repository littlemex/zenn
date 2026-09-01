---
title: "Basic11 - 安全に破棄する"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.1](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.1)

本章では、これまで構築してきた Amazon EKS 基盤を安全に破棄します。この章の目的はシンプルで、立てたリソースをすべて漏れなく削除し、消えたことを確認することです。アクセラレータノードは時間単価が高いため、ワークショップ向けに試した後に破棄し損ねて課金だけが残る事故を防ぐことが狙いです。

# 解説

## これは何をするものか

破棄で気をつけるべき点は 1 つです。NodePool を消しても、実際のノードの drain・Amazon EC2 インスタンスの終了・ENI の解放は Karpenter が引き継いで進めます。`k delete nodepool` はその後始末が終わるまで戻ってこないことがあり、Pod が退去できないなどで進まなくなると長く待たされます ([`04-teardown.sh`](https://github.com/littlemex/distributed-ai/blob/main/infra/eks/scripts/04-teardown.sh) の破棄経路がこの削除にタイムアウトを付けているのはそのためです)。この非同期処理が終わる前に Karpenter 本体やアクセラレータ関連のコントローラを消してしまうと、ノードを終了させるコントローラが無くなり、Amazon EC2 インスタンスが孤立して課金だけが続きます。

この事故を防ぐため、破棄は 2 段階で進めます。まずアクセラレータプールのワークロードとノードを片付け、ノードが実際に消えたことを確認してから、`terraform destroy` でクラスタ全体を破棄します。この順序と、`terraform destroy` の内部でノードの drain 完了を待つ仕組みは [`infra/eks`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks) が担保しているので、本章では手順の実行と確認に集中します。

## ストレージを残したい場合

現在の実装は、Amazon FSx for Lustre や Amazon EFS を含むすべてのリソースを 1 つの Terraform state で管理しており、`terraform destroy` はストレージも含めて丸ごと削除します。これは再現性を優先した使い捨て環境としての設計です。

本章の手順を使わず `terraform destroy` を直接実行した場合も、課金が残らないようにする仕掛けは働きます。ただしそのために destroy は Kubernetes 側に手を入れます。具体的には、全 namespace の TrainJob と全 NodePool を削除してから、NodeClaim が 0 になるのを待ちます。TrainJob が残っていると JobSet の Pod がノードを使い続けて待ちがタイムアウトし、NodePool が残っていると Karpenter がノードを作り続けて待ちが終わらないためです。`distai` 以外の namespace に置いた TrainJob も削除対象になるので、消えて困るものがあるなら destroy より先に退避してください。

クラスタだけ壊してストレージを残す運用と、既存のファイルシステムを次のクラスタで再利用する運用は、現在の実装では扱えません。長期保持したいデータは破棄の前に Amazon S3 などへ退避してください。

# ワークショップ実施

:::message alert
この章はクラスタを破棄します。Advanced02 のプロファイリング基盤など、稼働中のクラスタを前提にする章を実施する予定がある場合は、**この章より先にそちらを済ませてください**。破棄したあとに実施するには Basic01 からの再構築が必要になります。
:::

はじめにシェルを対象クラスタへ向けます。Basic01 手順 3 と同じ 4 行で、`CLUSTER_NAME` と `AWS_REGION`、それに 1 行目のチェックアウトのパスは自分のものに読み替えます。

```bash
cd ~/distributed-ai-v0.2.1
export CLUSTER_NAME=distai-eks
export AWS_REGION=us-east-2
source infra/scripts/distai-env.sh
```

前提は次のとおりです。

| 前提 | どこで用意するか |
|---|---|
| 破棄したいクラスタに context が向いていること | [Basic01](https://zenn.dev/tosshi/books/eks-distributed-ai/viewer/eks-vpc-foundation) の手順 3 の 4 行 |

## 1. 前提を確認する

この章の前提は機械が判定できません。次のコマンドの出力を自分の目で照合します。

```bash
k config current-context
aws eks describe-cluster --name "$CLUSTER_NAME" --region "$AWS_REGION" --query 'cluster.{name:name,status:status}' --output text
```

表示されたクラスタ名が破棄したいものであることを、自分の目で確かめてから進んでください。`k` の向き先は 4 行が `CLUSTER_NAME` から作るので、`CLUSTER_NAME` を読み替え忘れたまま実行すると、context も揃って別のクラスタを指します。ここは機械が判定できない唯一の前提です。

この章の操作はクラスタ全体に影響する破壊的操作です。`AWS_REGION` は手順 5 の孤児ボリュームの確認でそのまま使うので、ここが違っていると別のリージョンを照会して「残っていない」という答えが返ってきます。`04-teardown.sh` 自身も先頭で Terraform の出力からクラスタ名を読み取り、context がそのクラスタを指しているかを検証して、違えば破壊的な手順の前に中断します。

## 2. アクセラレータプールをドレインする

スクリプトは `charts` と同じく相対パスで置かれているので、`infra/eks` から実行します。削除対象の namespace は `NAMESPACE` から受け取ります。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
export NAMESPACE=distai
```

`04-teardown.sh` を `--destroy` なしで実行すると、指定した namespace のワークロードを削除し、アクセラレータプールのノードを Karpenter に回収させます。作業用 namespace は Basic01 以降で使ってきた `distai` です。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks/scripts
./04-teardown.sh --namespace "$NAMESPACE"
```

削除対象の NodePool は、アクセラレータプールの device taint（`nvidia.com/gpu` / `aws.amazon.com/neuron`）を持つものをクラスタに問い合わせて自動的に見つけます。`accelerator_pools` は読者が自分で定義するマップなので、スクリプトが特定のプール名を固定値で指定することはありません。CPU プールは、`terraform destroy` 自身が最後に流す処理の実行先として要るので、この手順では対象外です (手順 4 のスクリプトが `terraform destroy` に入る直前にまとめて削除します)。

クラスタは残したまま namespace の PVC も片付けたい場合は `--delete-pvcs` を付けます。その namespace の PVC を storage の種類によらず一括削除し、削除が Pod で止まったときはどの Pod が使用しているかを表示します。本書が用意する共有ストレージの PV は `Retain` なので、ファイルシステムのデータは消えず、PVC のバインドだけが外れて PV は `Released` になります。ただしこのフラグは namespace の PVC を種類によらず全部消すので、自分で作った動的プロビジョニングの PVC (`storageClassName: gp3` など、`reclaimPolicy: Delete` の PV を持つもの) があると、その EBS ボリュームは中身ごと削除されます。残したいものが無いかを `k get pvc -n "$NAMESPACE"` で確認してから付けてください。クラスタごと破棄する場合も、PVC は自動では削除されません。`--destroy` の経路に PVC を削除する処理はなく、読者が作った PVC は Terraform の管理下にもないからです。Basic10 の手順 6 で見たとおり、PVC が `Bound` のまま残っていると PV の削除がファイナライザで止まり、`terraform destroy` がそこで停滞し得ます。クラスタごと捨てる場合も `--delete-pvcs` を付けるか、Basic10 手順 6 と同じ手順で先に PVC を消しておくのが安全です。

対話実行なので、各ステップの前に `y/N` の確認が入ります。実行後に、device リソースを持つノードが残っていないかが表示されます。

```text
Discovered accelerator NodePool(s): gpu-ddp
...
Accelerator nodes still registered:
  none
```

:::message alert
プールを 1 つでも取り残すと、そのノードは課金され続けます。GPU や Neuron は時間単価が高いので、「Accelerator nodes still registered」が `none` になるまで確認してから次に進んでください。ノードは非同期に消えるため、`k get nodeclaims -l karpenter.sh/nodepool=gpu-ddp -w` のようにアクセラレータプール名で絞って消えていく様子を追えます。
:::

## 3. アクセラレータノードのドレイン完了を確認する

`POOL` は手順 2 の `Discovered accelerator NodePool(s):` に出たプール名です。複数あった場合は 1 つずつ見るか、`k get nodeclaims -w` で全体を見ます。

```bash
POOL=gpu-ddp
k get nodeclaims -l "karpenter.sh/nodepool=$POOL" -w
```

`-w` は watch なので、対象が空になってもコマンドは終わりません。行が消えたのを見たら `Ctrl-C` で抜けます。プロンプトに戻る形で待ちたい場合は次を使います。

```bash
while k get nodeclaims -l "karpenter.sh/nodepool=$POOL" --no-headers 2>/dev/null | grep -q .; do sleep 15; done
```

NodePool 削除の直後は、そのプールの NodeClaim が `Terminating` で残り、Karpenter が Amazon EC2 インスタンスの終了をバックグラウンドで進めます。単一ノードなら概ね 9 分前後で消えます。EFA を複数枚持つ大きなノードは ENI の解放に時間がかかり、さらに長くかかることがあります。

ここで確認したいのは、手順 2 で削除したアクセラレータプールの NodeClaim が消えることです。上のコマンドはプール名で絞っているので、observability の章を実施していて `monitoring` NodePool の NodeClaim が残っていても表示されません。それは高価なアクセラレータではなく、次の手順 4 でクラスタごと破棄されるので待つ必要がありません。絞り込んだ一覧が空になったら破棄に進みます。

## 4. クラスタ全体を破棄する

破棄の前に、作業 namespace に PVC が残っていないかを見ます。残っている場合は `--delete-pvcs` も付けます。付けないと `Bound` の PV がファイナライザで残り、`terraform destroy` がそこで停滞し得ます。

```bash
k get pvc -n "$NAMESPACE"
./04-teardown.sh --namespace "$NAMESPACE" --delete-pvcs --destroy
```

PVC が 1 つも無ければ `--delete-pvcs` は不要です。逆に、自分で作った動的プロビジョニングの PVC が残っている場合、このフラグはその EBS ボリュームを中身ごと消すので、手順 2 の注意を読んでから付けてください。

`--destroy` を付けると、ワークロードとノードの片付けに続けて `terraform destroy` が走ります。この VPC の中にこの state が管理していないもの (他チームの EFS マウントターゲットやロードバランサ、別の state が作ったセキュリティグループなど) があると、subnet や VPC の削除が拒否されて destroy は終盤で失敗します。スクリプトはこれを事前に点検しないので、失敗したら Terraform のエラーに出るリソース ID を手がかりに、残っているものを自分で確認して片付けます。この段階まで来ると EKS クラスタ自体はすでに消えているので、やり直しは `cd infra/eks && terraform destroy` を直に実行するのが確実です。このとき `04-teardown.sh` は、手順 2 で消したアクセラレータプールに加えて、`monitoring` などクラスタに残っている Karpenter の NodePool もすべて先に削除します。これは、NodePool が残っていると Karpenter が Pod を載せるためにノードを作り続け、`terraform destroy` が内部で待つノードのドレイン完了がいつまでも来なくなるためです。すべての NodePool を止めたうえで `terraform destroy` に入り、Karpenter がノードを終了し終えるのを待ってから、Karpenter 本体やアクセラレータ関連のコントローラを破棄します。この順序により、アクセラレータノードが終了されないまま課金だけが残る事態を防ぎます。

:::message alert
ノードのドレイン待ちが進まない場合、よくある原因は [`karpenter.sh/do-not-disrupt`](https://karpenter.sh/docs/concepts/disruption/#pod-level-controls) を付けた Pod です。Karpenter はこの Pod を退去させないので、載っているノードが空になりません。手順 2 が片付けるのは `--namespace` で指定した namespace だけなので、それ以外の namespace に置いたものは残ります。Advanced01 の headroom Deployment (`kube-system`) がその例で、消し忘れると待ちが終わりません。この待ちは `terraform destroy` の中で NodeClaim が 0 になるまで最大 30 分続き、原因の Pod を名前で教えてはくれません。0 にならなければ destroy は中断されるので、待ちが進まないときは `k get pods -A` に残っている Pod の annotation を見て、`do-not-disrupt` が付いたものを自分で消してください。

スクリプトの `y/N` を通ったあと、最後に Terraform 自身が `Do you really want to destroy all resources?` と聞いてきます。ここで受け付けるのは `yes` という綴りだけで、`y` では取り消しになります。NodePool を消したあとで取り消すと、ノードは消えたのにクラスタが残る中途半端な状態になるので、`yes` と入力してください。破棄全体は 20〜30 分が目安です。

`terraform destroy` の完了まで、ターミナルを閉じずに待ちましょう。途中で中断すると、アクセラレータノードが取り残されて課金が続く可能性があります。破棄が完了したら、Amazon EC2 コンソールや `aws ec2 describe-instances` で対象リソースが残っていないことを最終確認すると確実です。
:::

非対話で流す場合は `--yes` を付けます。これはスクリプト自身の確認を自動で通すだけでなく、`terraform destroy` にも `-auto-approve` を渡します。付け忘れると、ワークロードとノードは消えたのに `terraform destroy` が確認待ちで止まり、クラスタだけ残る中途半端な状態になりかねないため、バックグラウンド実行では必須です。

## 5. 残るものを確認する

`terraform destroy` が消すのはクラスタの Terraform state が管理しているリソースだけです。残るものは 2 種類あります。意図せず取り残されるものが 1 つと、意図的に残すものが 3 つです。

意図せず残る 1 つは孤児 EBS です。`monitoring` namespace の Prometheus と Grafana は StatefulSet の volumeClaimTemplate による動的 EBS を持ちます。`terraform destroy` は Helm リリースを消しますが、この経路で作られた PVC は消さないため、**EBS ボリュームだけが AWS 側に取り残されます**。実際に破棄済みのクラスタ 7 つ分で 21 本 (計 620 GiB) の孤児ボリュームが残っていた実例があるので、破棄後に必ず確認してください。

```bash
aws ec2 describe-volumes --region "$AWS_REGION" --filters "Name=status,Values=available" \
  --query 'Volumes[].[VolumeId,Size,Tags[?Key==`Name`]|[0].Value]' --output text
```

名前に破棄したクラスタ名と `dynamic-pvc` を含むものが該当します。中身は Prometheus の時系列と Grafana の設定なので、残す必要がなければ `aws ec2 delete-volume --region "$AWS_REGION" --volume-id <id>` で消します。

ここからが意図的に残すものです。1 つ目は state 自身の置き場です。state のバケットとロックテーブルはアカウントとリージョンごとに 1 つで、同じアカウントの他のクラスタも同じものを使うため、クラスタの破棄では消しません。2 つ目はレジストリのパラメータで、これも残ります。同じ名前でもう一度立てるなら、そのまま [`distai-up.sh`](https://github.com/littlemex/distributed-ai/blob/main/infra/scripts/distai-up.sh) を実行すれば同じ state の場所を再利用します。3 つ目はローカルの kubeconfig (`~/.kube/distai/<クラスタ名>.<namespace>.yaml`) で、課金には関係しないので放置してかまいません。

そのクラスタを二度と使わない場合は、レジストリのパラメータだけ消しておけます。

```bash
aws ssm get-parameters-by-path --path "/distai/v1/clusters/$CLUSTER_NAME" --recursive \
  --query 'Parameters[].Name' --output text | tr '\t' '\n' > /tmp/distai-registry-params.txt
if [ -s /tmp/distai-registry-params.txt ]; then
  xargs -n 10 aws ssm delete-parameters --names < /tmp/distai-registry-params.txt
else
  echo "消すパラメータはありません"
fi
```

:::message alert
Advanced02 のプロファイリング基盤を導入した場合、そのデータ層 (SageMaker MLflow、trace バケット、S3 Files) はクラスタとは別の Terraform state にあるため、本章の破棄では消えず課金が続きます。データ層は消してはいけない記録なのでクラスタの寿命とは切り離してあります。不要になったら Advanced02 の後片付けの手順で明示的に削除してください。
:::

# まとめ

本章では、`04-teardown.sh` を使ってアクセラレータプールをドレインし、ノードが消えたことを確認してから `terraform destroy` でクラスタ全体を破棄しました。ポイントは、Kubernetes API の受理と実際のリソース削除は別物であり、ノードが本当に消えたことを確認してから次に進むことです。この順序を守れば、GPU や Neuron のような高額なリソースを課金を残さず安全に削除できます。state の置き場とレジストリの記録は同じ名前で再構築するために残るので、二度と使わない場合だけ消してください。

# 今後の改善

| なぜ改善すべきか | 改善対象 | 改善案 |
|---|---|---|
| すべてを 1 つの state で管理しているため、クラスタを壊すとストレージも消え、長期保持したいデータを退避する手間が毎回かかる | ストレージの寿命 | ストレージをクラスタとは別の state に切り出す |
| 既存ファイルシステムを次のクラスタで再利用する変数が無いので、作り直すたびにデータを入れ直すことになる | 既存ファイルシステムの参照 | 作成をスキップして既存 ID を参照する変数を用意し、ネットワーク到達性の確保も含めて扱う |

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [Karpenter NodeClaim/NodePool](https://karpenter.sh/docs/concepts/)
- [Terraform: Resource dependencies (depends_on)](https://developer.hashicorp.com/terraform/language/resources/behavior#resource-dependencies)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
