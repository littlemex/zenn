---
title: "Basic11 - 安全に破棄する"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.0)

本章では、これまで積み上げてきた Amazon EKS 基盤を安全に破棄します。この章の目的はシンプルで、立てたリソースをすべて綺麗に消し、消えたことを確認することです。アクセラレータノードは時間単価が高いため、ワークショップ向けに試した後に破棄し損ねて課金だけが残る事故を防ぐことが狙いです。

# 解説

## これは何をするものか

破棄で気をつけるべき点は 1 つです。`kubectl` での NodePool 削除は Kubernetes API がリクエストを受理した瞬間に完了扱いになりますが、実際のノードの drain・Amazon EC2 インスタンスの終了・ENI の解放は Karpenter が非同期に進めます。この非同期処理が終わる前に Karpenter 本体やアクセラレータ関連のコントローラを消してしまうと、ノードを終了させる担当がいなくなり、Amazon EC2 インスタンスが孤立して課金だけが続きます。

この事故を防ぐため、破棄は 2 段階で進めます。まずアクセラレータプールのワークロードとノードを片付け、ノードが実際に消えたことを確認してから、`terraform destroy` でクラスタ全体を壊します。この順序と、`terraform destroy` の内部でノードの drain 完了を待つ仕組みは [`infra/eks`](https://github.com/littlemex/distributed-ai/tree/main/infra/eks) が担保しているので、本章では手順の実行と確認に集中します。

## ストレージを残したい場合

現在の実装は、Amazon FSx for Lustre や Amazon EFS を含むすべてのリソースを 1 つの Terraform state で管理しており、`terraform destroy` はストレージも含めて丸ごと削除します。これは再現性を優先した使い捨て環境としての設計です。

クラスタだけ壊してストレージを残したい、あるいは次に立てるクラスタで既存のファイルシステムを再利用したい、という要求も実運用では出てきます。結論として、どちらも現在の実装では対応していません。

- **ストレージだけ残す** — ストレージをクラスタとは別の Terraform state に切り出す、破棄の直前に対象リソースを state から外す、`prevent_destroy` を設定するといった方法が一般にはありますが、いずれも現在の実装には入っていません。長期保持したいデータがある場合は、破棄の前に Amazon S3 などへ退避するのが現実的です。
- **既存ファイルシステムの再利用** — 静的プロビジョニングの仕組み自体は、PersistentVolume の `volumeHandle` に既存ファイルシステムの ID を指定すれば技術的には成立します。ただし作成をスキップして既存 ID を参照するための変数は用意されておらず、同一 VPC 内であればセキュリティグループの追加、別 VPC であれば VPC ピアリングなどのネットワーク到達性の確保が別途必要になります。

これらを恒久的に運用へ組み込むかどうかは今後の検討事項です。本章の手順は、あくまで環境を綺麗に消し切ることを前提にしています。

# ワークショップ実施

## 1. 前提を確認する

共有クラスタでは、破棄の前に必ず操作対象のクラスタを確認します。この章の操作はクラスタ全体に影響する破壊的操作なので、意図しないクラスタへの誤操作を避けます。

```bash
cd ~/distributed-ai-v0.2.0
export CLUSTER_NAME=distai-eks
export AWS_REGION=us-east-2
source infra/scripts/distai-env.sh
```

`CLUSTER_NAME` は自分のクラスタ名に読み替えます。表示された context が破棄したいクラスタであることを確認してから進みます。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks
export NAMESPACE=distai
```

`04-teardown.sh` は先頭で Terraform の出力からクラスタ名を読み取り、`kubectl` の現在のコンテキストがそのクラスタを指しているかを検証します。別のクラスタを指していた場合は、破壊的な手順に進む前に中断します。

## 2. アクセラレータプールをドレインする

`04-teardown.sh` を `--destroy` なしで実行すると、指定した namespace のワークロードを削除し、アクセラレータプールのノードを Karpenter に回収させます。作業用 namespace は Basic01 以降で使ってきた `distai` です。

```bash
cd "$(git rev-parse --show-toplevel)"/infra/eks/scripts
./04-teardown.sh --namespace "$NAMESPACE"
```

削除対象の NodePool は、アクセラレータプールの device taint（`nvidia.com/gpu` / `aws.amazon.com/neuron`）を持つものをクラスタに問い合わせて自動的に見つけます。`accelerator_pools` は読者が自分で定義するマップなので、スクリプトが特定のプール名を決め打ちすることはありません。CPU プールは Karpenter コントローラなどの実行先として残す必要があるため対象外で、次の `terraform destroy` でまとめて消えます。

クラスタは残したまま namespace の PVC も片付けたい場合は `--delete-pvcs` を付けます。その namespace の PVC を storage の種類によらず一括削除し、削除が Pod で止まったときはどの Pod が掴んでいるかを表示します。PV は `Retain` なのでファイルシステムのデータは消えず、PVC のバインドだけが外れて PV は `Released` になります。クラスタごと破棄する場合はこのフラグは不要で、次の `--destroy` が PVC ごとまとめて消します。

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

```bash
k get nodeclaims -w
```

NodePool 削除の直後は、そのプールの NodeClaim が `Terminating` で残り、Karpenter が Amazon EC2 インスタンスの終了をバックグラウンドで進めます。単一ノードなら概ね 9 分前後で消えます。EFA を複数枚持つ大きなノードは ENI の解放に時間がかかり、さらに長くかかることがあります。

ここで確認したいのは、手順 2 で削除したアクセラレータプールの NodeClaim が消えることです。observability の章を実施している場合は監視スタック用の `monitoring` NodePool の NodeClaim が残りますが、これは高価なアクセラレータではなく、次の手順 4 でクラスタごと破棄されるので、ここで 0 件になるのを待つ必要はありません。アクセラレータプールの NodeClaim が消えたら破棄に進みます。

## 4. クラスタ全体を破棄する

```bash
./04-teardown.sh --namespace "$NAMESPACE" --destroy
```

`--destroy` を付けると、ワークロードとノードの片付けに続けて `terraform destroy` が走ります。このとき `04-teardown.sh` は、手順 2 で消したアクセラレータプールに加えて、`monitoring` などクラスタに残っている Karpenter の NodePool もすべて先に削除します。これは、NodePool が残っていると Karpenter が Pod を載せるためにノードを作り続け、`terraform destroy` が内部で待つノードのドレイン完了がいつまでも来なくなるためです。すべての NodePool を止めたうえで `terraform destroy` に入り、Karpenter がノードを終了し終えるのを待ってから、Karpenter 本体やアクセラレータ関連のコントローラを破棄します。この順序により、アクセラレータノードが終了されないまま課金だけが残る事態を防ぎます。

:::message alert
`terraform destroy` の完了まで、ターミナルを閉じずに待ちましょう。途中で中断すると、アクセラレータノードが取り残されて課金が続く可能性があります。破棄が完了したら、Amazon EC2 コンソールや `aws ec2 describe-instances` で対象リソースが残っていないことを最終確認すると確実です。
:::

非対話で流す場合は `--yes` を付けます。これはスクリプト自身の確認を自動で通すだけでなく、`terraform destroy` にも `-auto-approve` を渡します。付け忘れると、ワークロードとノードは消えたのに `terraform destroy` が確認待ちで止まり、クラスタだけ残る中途半端な状態になりかねないため、バックグラウンド実行では必須です。

## 5. 残るものを確認する

`terraform destroy` が消すのはクラスタの Terraform state が管理しているリソースだけです。次の 3 つは意図的に残ります。

1 つ目は state 自身の置き場です。state のバケットとロックテーブルはアカウントとリージョンごとに 1 つで、同じアカウントの他のクラスタも同じものを使うため、クラスタの破棄では消しません。2 つ目はレジストリのパラメータで、これも残ります。同じ名前でもう一度立てるなら、そのまま `distai-up.sh` を実行すれば同じ state の場所を再利用します。3 つ目はローカルの kubeconfig (`~/.kube/distai/<クラスタ名>.<namespace>.yaml`) で、課金には関係しないので放置してかまいません。

そのクラスタを二度と使わない場合は、レジストリのパラメータだけ消しておけます。

```bash
aws ssm get-parameters-by-path --path "/distai/v1/clusters/$CLUSTER_NAME" --recursive \
  --query 'Parameters[].Name' --output text | tr '\t' '\n' > /tmp/distai-registry-params.txt
xargs -n 10 aws ssm delete-parameters --names < /tmp/distai-registry-params.txt
```

:::message alert
Advanced02 のプロファイリング基盤を導入した場合、そのデータ層 (SageMaker MLflow、trace バケット、S3 Files) はクラスタとは別の Terraform state にあるため、本章の破棄では消えず課金が続きます。データ層は記録の正本なのでクラスタの寿命とは切り離してあります。不要になったら Advanced02 の後片付けの手順で明示的に畳んでください。
:::

# まとめ

本章では、`04-teardown.sh` を使ってアクセラレータプールをドレインし、ノードが消えたことを確認してから `terraform destroy` でクラスタ全体を破棄しました。ポイントは、Kubernetes API の受理と実際のリソース削除は別物であり、ノードが本当に消えたことを確認してから次に進むことです。この順序を守れば、GPU や Neuron のような高額なリソースを課金を残さず安全に畳めます。state の置き場とレジストリの記録は同じ名前で立て直すために残るので、二度と使わない場合だけ消してください。ストレージを残す運用や既存ファイルシステムの再利用は現在の実装では対応しておらず、必要なデータは破棄の前に退避してください。

# 参考資料

- [Amazon EKS ユーザーガイド](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [Karpenter NodeClaim/NodePool](https://karpenter.sh/docs/concepts/)
- [Terraform: Resource dependencies (depends_on)](https://developer.hashicorp.com/terraform/language/resources/behavior#resource-dependencies)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
