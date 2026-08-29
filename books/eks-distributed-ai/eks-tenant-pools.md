---
title: "Experiment01 - karpenter-tenant-pools でプールをセルフサービス化する"
free: true
---

GitHub Tag: [release/eks-distributed-ai/v0.2.0](https://github.com/littlemex/distributed-ai/tree/release/eks-distributed-ai/v0.2.0)

本章では、Basic04 と Basic05 で Terraform の `accelerator_pools` 変数として定義していた GPU プール（g5 の On-Demand と p5 の Capacity Block）を、`karpenter-tenant-pools` という OSS の operator を使って namespace 単位の CRD から立ち上げます。プールの定義を Terraform の共有変数から切り離し、チームが自分の namespace のなかだけで自己完結してプールを作れるようにするのが狙いです。

:::message
本章は実験的な位置づけで、Basic シリーズの Terraform ベースの構成とは別の運用方式を試すものです。Basic04 までで Karpenter が導入済みのクラスタがあることを前提にします。プールを CRD で作る点だけが変わり、Karpenter 本体・device plugin・ネットワーク基盤は Basic03 までで作ったものをそのまま使います。
:::

# 解説

## 全体構成

![Amazon EKS 分散 AI 基盤の全体アーキテクチャ](/images/books/eks-distributed-ai/arch-overview.png)

本章で扱うのは、この図のうち Karpenter の NodePool / EC2NodeClass を「誰がどう定義するか」の部分です。ノードが起動したあとの EFA・GPU・共有ストレージといった要素は Basic シリーズで作ったものをそのまま使います。変えるのは、プール定義の入り口を Terraform の共有変数から、namespace に属する CRD に置き換える点だけです。

## これは何をするものか

Karpenter の `NodePool` と `EC2NodeClass` はどちらもクラスタスコープのリソースです。つまりプールに「所有者」という概念がなく、あるチームの Pod が別のチームの（多くは Capacity Block で予約した高価な）ノードに載るのを、仕組みとして止める手段が標準では存在しません。Basic04 のようにプール定義を 1 つの Terraform map 変数（`accelerator_pools`）に集約する方式は、単一の管理者が全プールを面倒みる分にはよいのですが、2 つのチームがそれぞれ独立にプールを足そうとすると同じ変数への二重代入になり、共有ファイルを手で編集し合うことになります。これはマルチテナントのワークフローとは言えません。

`karpenter-tenant-pools`（以降 operator）は、namespace に属する `AcceleratorPool` という CRD を 1 つ受け取り、それを Karpenter の `NodePool` + `EC2NodeClass` のペアに変換します。テナント分離は、VAP を有効にした既定構成で、かつ除外ラベルを付ける権限を管理している限り、仕組みとして迂回できない形で強制されます。テナントを表す taint とラベルは CR の namespace から導出され（ユーザ入力からは決して作られません）、プール名は namespace と CR 名から一意に導出され（ハッシュサフィックス付きで、なりすましや衝突ができません）、同梱の `ValidatingAdmissionPolicy`（VAP）が「別テナントのノードを狙う toleration を持つ Pod」を拒否します。チームは namespace の RBAC さえ持てばプールを自己申請でき、`karpenter.sh` の API への書き込み権限を一切渡さずに済みます。

operator 自身は AWS を呼びません。EC2 を起動するのは Karpenter の仕事であり、operator は CRD から CRD へ変換するだけなので、仮に operator が侵害されても AWS のリソースが直接作られることはありません。ただし Kubernetes 側の権限は必要で、Karpenter の NodePool と EC2NodeClass、そして自身が扱う `AcceleratorPool` への書き込み権限を持ちます。つまり「AWS の権限は持たないが、Karpenter に何を作らせるかは書ける」という位置づけです。この「credential-free」な設計が、権限を絞ったままセルフサービスを実現する前提になっています。

## AcceleratorClass と AcceleratorPool の分担

operator が扱う CRD は 2 種類あり、責務がはっきり分かれています。

`AcceleratorClass`（クラスタスコープ、管理者が所有）は、プラットフォーム側の既定値を持ちます。ノードの IAM ロール、AMI セレクタ、サブネット／セキュリティグループのセレクタ、タグ、そして「どの namespace がプールを作ってよいか」の allowlist とクォータ（テナント表）を定義します。ここに書かれる値はすべて管理者が管理する信頼された情報で、テナントは触れません。

`AcceleratorPool`（namespace スコープ、テナントが所有）は、テナントが実際に欲しいプールを宣言します。アクセラレータの種類（`nvidia` か `neuron`）、インスタンスファミリ、購入オプション（On-Demand / Spot / Reserved）、AZ、上限（`limits`）、参照する Capacity Block の ID を書きます。ロールや AMI やサブネットといったプラットフォーム値は一切書きません。それらは `classRef` で参照する `AcceleratorClass` から来ます。

この分担により、テナントが書けるのは「どんなプールが欲しいか」だけになり、「どの IAM ロールでノードを起動するか」のような特権的な設定はテナントの手の届かないところに置かれます。実際のセルフサービス運用では、テナントの namespace には `acceleratorpools` の CRUD だけを許す Role/RoleBinding を渡し、`AcceleratorClass` と `karpenter.sh` API の操作権限は管理者だけが持ちます。本章では手順を追いやすくするため、管理者とテナントの両方の操作を同じ管理者権限で実行します。

## テナント境界はどう強制されるか

境界は 3 つの経路で守られます。このうち Pod のスケジューリングに関わるのが 2 つ (taint と VAP) で、残る 1 つは Capacity Block へのアクセス制御です。

1 つ目は生成物の側です。operator が作る NodePool には、CR の namespace から導出した 既定では `tenantpools.dev/tenant=<namespace>` という taint が必ず載ります (キーは `tenantLabelKey` で変えられます)。つまりこのプールのノードには、同じ値の toleration を持つ Pod しか載れません。

2 つ目は Pod の側です。taint に対応する toleration は Pod が自由に書けてしまうので、それだけでは「別テナントの値を勝手に tolerate する Pod」を止められません。そこで operator は `ValidatingAdmissionPolicy` を同梱し、Pod が自分の namespace 以外のテナント値を tolerate しようとした場合、あるいはワイルドカードの toleration を持つ場合に、その Pod の作成を拒否します。この検査は作成だけでなく更新にも当たるので、すでに走っている Pod でも、条件に触れる状態のまま更新しようとすると拒否されます。taint（スケジューラ側）と VAP（admission 側）の両方がそろって初めて、境界が塞がれます。

なお、`aws-node` や device plugin のような system 系の DaemonSet はワイルドカードの toleration を持つため、VAP をそのまま全 namespace に適用すると新規ノードに CNI が載らず永久に `NotReady` になります。この意図しない動作を避けるため、VAP のバインディングは既定で `kube-system` などの system namespace を名前で除外しています。あわせて、任意の namespace に `tenantpools.dev/excluded=true` ラベルを付けると除外できる仕組みもあります。運用上の逃げ道として必要なものですが、テナント境界を素通りさせる手段でもあるので、このラベルを誰が付けられるかは境界そのものと同じ重みで管理する必要があります。

## この本の Terraform 版との関係

Basic04 の Terraform 版と本章の operator 版は、最終的にどちらも Karpenter の `NodePool` + `EC2NodeClass` を作る点は同じで、生成される requirements（`instance-gpu-manufacturer` や `capacity-type` など）もほぼ同じ形になります。違うのは「誰がプール定義を書き、どう境界を守るか」です。Terraform 版は管理者が共有変数を一元管理する集中型、operator 版はテナントが namespace ごとに自己申請する分散型です。

operator は Karpenter 本体・device plugin・Capacity Block の購入には関与しません。Karpenter のインストールは公式チャートで（Basic03 で完了済み）、device plugin の導入と CB の購入は引き続き利用者の責務であり、CB は Basic05 と同じ手順で購入します。本章はあくまで「プール定義の入り口」を CRD に置き換える実験です。

# ワークショップ実施

本章の実機検証は、Basic シリーズと同じ構成の検証用クラスタ（us-east-2、Karpenter v1.13、`ReservedCapacity` フィーチャゲート有効）で実施しました。g5 プールは On-Demand、p5 プールは購入済みの p5.48xlarge の Capacity Block（`cr-...`）を使います。以降のコマンド出力はこの構成の実測値で、Capacity Block の AZ（`us-east-2a`）は読者の予約に合わせて読み替えてください。

## 1. 前提を確認する

operator は Karpenter v1 が入っているクラスタで動きます。既定で有効な VAP は [Kubernetes 1.30 以上](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/)を要求するので、クラスタのバージョンも確認します。

```bash
k version -o json | jq -r .serverVersion.gitVersion
```

Capacity Block を Reserved プールで使う場合は、Karpenter コントローラの [`ReservedCapacity` フィーチャゲート](https://karpenter.sh/docs/reference/settings/)が有効である必要があります（このフィーチャゲートを前提に、Capacity Block は Karpenter v1.6 以降、ODCR は v1.3 以降で使えます）。

```bash
k -n karpenter get deploy karpenter \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
k -n karpenter get deploy karpenter \
  -o jsonpath='{range .spec.template.spec.containers[0].env[?(@.name=="FEATURE_GATES")]}{.value}{"\n"}{end}'
```

実機出力:

```text
public.ecr.aws/karpenter/controller:1.13.0@sha256:...
ReservedCapacity=true,SpotToSpotConsolidation=false,NodeRepair=false,NodeOverlay=false,StaticCapacity=false
```

`ReservedCapacity=true` が確認できれば、CB を参照するプールも動きます。あわせて、ノードを起動する IAM ロール名と、サブネット／セキュリティグループを選ぶための discovery タグを控えておきます。これらは次の手順で `AcceleratorClass` に渡します。この本の Terraform 構成では、ロールは `<cluster>-karpenter-node`、discovery タグは `karpenter.sh/discovery: <cluster>` です。

```bash
k get ec2nodeclass gpu-p5 \
  -o jsonpath='role={.spec.role}{"\n"}instanceProfile={.spec.instanceProfile}{"\n"}subnet={range .spec.subnetSelectorTerms[*]}{.tags}{end}{"\n"}'
```

実機出力:

```text
role=
instanceProfile=distai-eks-0807-karpenter-node
subnet={"karpenter.sh/discovery":"distai-eks-0807"}
```

ここで参照した `gpu-p5` は Basic04/05 で定義したプール名の一例です。読者の環境では別名のことがあるので、`k get ec2nodeclass` で存在する名前を確認して読み替えてください。この本の Terraform 版は `instanceProfile` を直接指定しているため `role` は空ですが、operator の `AcceleratorClass` は `role`（IAM ロール名）を受け取り、そのロールから Karpenter がインスタンスプロファイルを作ります。この検証環境では、インスタンスプロファイル `distai-eks-0807-karpenter-node` の背後にある同名の IAM ロール `distai-eks-0807-karpenter-node` がそのロールにあたります。次の手順ではこのロール名と discovery タグ `karpenter.sh/discovery: distai-eks-0807` を `AcceleratorClass` に渡します。

:::message
本書は作業用 namespace を `distai` に統一していますが、本章はテナント分離のデモが目的のため、テナント役の専用 namespace `team-gpu` を使います（`distai` 統一ルールの意図的な例外です）。
:::

## 2. operator を helm で導入する

operator は Helm チャートで導入します。CRD もチャートに同梱されています。なおこの operator は早期開発中で、CRD の API バージョンは `v1alpha1` です。今後 API が変わりうるので、本章の手順は長期運用の前提としては読まないでください。既定で VAP が有効になるため、クラスタは Kubernetes 1.30 以上である必要があります（VAP が GA になったバージョン。手順 1 で確認済みです）。1.29 以下では `helm install` の時点でチャートが明示的に失敗します。どうしても古いクラスタで試す場合は `--set policies.validating.enabled=false` で VAP を切れますが、その場合 Pod 側の境界は taint だけになり、後述の admission による拒否は働きません。

```bash
helm install ktp oci://ghcr.io/littlemex/karpenter-tenant-pools/chart \
  --namespace tenantpools-system --create-namespace \
  --version 0.1.1
```

:::message
本書の検証環境では、operator イメージをアカウント内の Amazon ECR にミラーして使いました。その場合は `--set image.repository=<account>.dkr.ecr.<region>.amazonaws.com/karpenter-tenant-pools --set image.tag=<tag>` を付けます。イメージを差し替えても VAP と CRD の挙動は変わりません。
:::

導入できたら、operator の Pod と CRD、VAP が入ったことを確認します（以下の実機出力は検証を続けたクラスタで後日採取したものなので `AGE` が経過していますが、導入直後は数十秒から数分の値になります）。

```bash
k -n tenantpools-system get deploy,pod
```

実機出力:

```text
NAME                                         READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/ktp-karpenter-tenant-pools   1/1     1            1           27h

NAME                                              READY   STATUS    RESTARTS   AGE
pod/ktp-karpenter-tenant-pools-7946f85d6d-dgpq7   1/1     Running   0          21h
```

CRD が入っていることも確認します。

```bash
k get crd | grep tenantpools.dev
```

実機出力:

```text
acceleratorclasses.tenantpools.dev              2026-08-08T01:33:40Z
acceleratorpools.tenantpools.dev                2026-08-08T01:33:40Z
```

pod-boundary の VAP が入っていることも確認します。

```bash
k get validatingadmissionpolicy | grep -E "NAME|tenant"
```

実機出力:

```text
NAME                                      VALIDATIONS   PARAMKIND   AGE
ktp-karpenter-tenant-pools-pod-boundary   4             <unset>     27h
```

operator の Deployment と Pod が `Running`、2 つの CRD、そして pod-boundary の VAP が出ていれば導入は成功です。

## 3. AcceleratorClass を作る（管理者）

管理者は、プラットフォーム側の既定値を持つ `AcceleratorClass` を 1 つ作ります。ここで手順 1 で控えた IAM ロールと discovery タグを渡します。`tenants` の allowlist に、プールを作ってよい namespace（ここでは `team-gpu`）を登録します。既定は `Deny` なので、登録しない namespace はプールを作れません。

```bash
k apply -f - <<'EOF'
apiVersion: tenantpools.dev/v1alpha1
kind: AcceleratorClass
metadata:
  name: distai-default
spec:
  role: distai-eks-0807-karpenter-node
  amiSelectorTerms:
    - alias: al2023@latest
  subnetSelectorTerms:
    - tags: { karpenter.sh/discovery: distai-eks-0807 }
  securityGroupSelectorTerms:
    - tags: { karpenter.sh/discovery: distai-eks-0807 }
  tags: { Environment: dev, ManagedBy: karpenter-tenant-pools }
  tenants:
    defaultPolicy: Deny
    entries:
      - namespaces: ["team-gpu"]
        maxPools: 4
EOF

k create namespace team-gpu --dry-run=client -o yaml | k apply -f -
```

`role`・`amiSelectorTerms`・`subnetSelectorTerms`・`securityGroupSelectorTerms` は、この本の Terraform 版が EC2NodeClass に書いていた値と同じものです。これらを管理者所有の Class 側に集約することで、テナントはこの値に触れずにプールを作れます。

## 4. g5 プールを作る（テナント）

テナントは自分の namespace（`team-gpu`）に `AcceleratorPool` を作ります。ここでは Basic04 で作った小型 GPU の On-Demand プールに相当するものを作ります (Basic04 は g6 と g5 を並べていますが、ここでは g5 に絞ります)。ロールや AMI は書きません。書くのは「nvidia の g5 を On-Demand で、GPU 上限 8 まで」という要望だけです。

:::message
本章の検証は、`AcceleratorPool` から NodePool / EC2NodeClass が正しく生成されるところ（Karpenter に受理される control-plane の契約）と、テナント境界が admission で機能するところまでを対象にします。実際に GPU ノードが起動する挙動は Basic04 と同じで、GPU を要求する Pod（テナント taint を tolerate するもの）を投入すると Karpenter がノードを起動します。ノード起動には後述の device plugin の注意（手順 6 の後）が関わるため、本章ではノードを起こさずに定義と境界の確認までを行います。
:::

:::message alert
Basic04 の Terraform 版で作った GPU プール（テナント taint を持たない従来の NodePool）がクラスタに残っていると、GPU を要求する Pod がそちらの旧プールに載ってしまい、テナント境界を素通りする可能性があります。operator 版のテナント分離を正しく検証したい場合は、Terraform 版の GPU プールを削除するか `limits` を 0 にして、operator が作るプールだけが GPU ノードを起動する状態にしてください。
:::

```bash
k apply -f - <<'EOF'
apiVersion: tenantpools.dev/v1alpha1
kind: AcceleratorPool
metadata:
  name: g5
  namespace: team-gpu
spec:
  classRef: distai-default
  accelerator: { kind: nvidia }
  instances:
    families: ["g5"]
    capacityTypes: ["on-demand"]
  limits:
    nvidia.com/gpu: "8"
EOF

k get acceleratorpool -n team-gpu g5 -o wide
```

実機出力:

```text
NAME   KIND     CLASS            READY   NODEPOOL                       AGE
g5     nvidia   distai-default   True    team-gpu-g5-c48cedec7057ba71   11s
```

`READY=True` になり、`NODEPOOL` 列に生成された NodePool 名が出ます。この NodePool 名はプールの namespace と名前から一意に導出され（ハッシュサフィックス付き）、テナントが名前を指定することはできません。生成された NodePool を見ると、operator がテナント taint と GPU の requirements を組み立てていることが分かります。

まず requirements を見ます。

```bash
k get nodepool team-gpu-g5-c48cedec7057ba71 \
  -o jsonpath='{range .spec.template.spec.requirements[*]}{.key}={.values}{"\n"}{end}'
```

実機出力:

```text
karpenter.k8s.aws/instance-gpu-manufacturer=[nvidia]
karpenter.k8s.aws/instance-family=[g5]
karpenter.sh/capacity-type=[on-demand]
```

次に taints を見ます。

```bash
k get nodepool team-gpu-g5-c48cedec7057ba71 \
  -o jsonpath='{range .spec.template.spec.taints[*]}{.key}={.value}{"\n"}{end}'
```

実機出力:

```text
tenantpools.dev/tenant=team-gpu
nvidia.com/gpu=
```

`tenantpools.dev/tenant=team-gpu` の taint が、CR の namespace から自動で載っている点が肝です。生成された EC2NodeClass 側には、手順 3 で Class に書いたロールとセレクタがそのまま入ります。

```bash
k get ec2nodeclass team-gpu-g5-c48cedec7057ba71 \
  -o jsonpath='role={.spec.role}{"\n"}ami={.spec.amiSelectorTerms}{"\n"}'
```

実機出力:

```text
role=distai-eks-0807-karpenter-node
ami=[{"alias":"al2023@latest"}]
```

生成された EC2NodeClass が Karpenter に受理されているかは、`Ready` condition で確認します。

```bash
k get ec2nodeclass team-gpu-g5-c48cedec7057ba71 \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status}{"\n"}{end}'
```

実機出力:

```text
Ready=True
```

`Ready=True` は、AMI・サブネット・セキュリティグループ・インスタンスプロファイルがすべて解決できたことを示します。ここで 1 点、`role` 指定が動く前提があります。Basic04 の Terraform 版は EC2NodeClass に `instanceProfile` を直接与えていましたが、operator は `role`（IAM ロール名）だけを与え、そのロールに対応するインスタンスプロファイルを Karpenter に作らせます。これには Karpenter コントローラのロールに `iam:CreateInstanceProfile` と `iam:AddRoleToInstanceProfile` の権限が必要です。この本の Terraform 構成の Karpenter コントローラにはこれらの権限が付与済みなので、`role` 指定でそのまま Ready になります。自前のクラスタでこれらの権限が無い場合、EC2NodeClass は Ready にならず、ノード起動時に失敗します。

ここまでで NodePool と EC2NodeClass の定義ができました。Basic04 の Terraform 版と同じく、この時点ではまだノードは立ちません。Karpenter は GPU を要求する Pod（Pending）が現れて初めてノードを起動します。実際にノードを起動するときは、テナント taint を tolerate する GPU Pod を投入します (生成された NodePool の taints に出ているとおり、実ノードを起動して載せる場合は `nvidia.com/gpu` のデバイス taint への toleration も併せて必要です)（この toleration は自分の namespace の値なので VAP に通ります。次の手順で確認します）。

## 5. p5 の Capacity Block プールを作る（管理者が allowlist、テナントが参照）

Basic05 で購入した p5.48xlarge の Capacity Block を、operator 経由で使います。Capacity Block へのアクセスは、テナントが勝手に参照できてはならないため、管理者が `AcceleratorClass` の該当 namespace エントリに CB の ID を明示的に allowlist した場合にだけ許可されます。まず管理者が Class に CB を追加します。

```bash
k apply -f - <<'EOF'
apiVersion: tenantpools.dev/v1alpha1
kind: AcceleratorClass
metadata:
  name: distai-default
spec:
  role: distai-eks-0807-karpenter-node
  amiSelectorTerms:
    - alias: al2023@latest
  subnetSelectorTerms:
    - tags: { karpenter.sh/discovery: distai-eks-0807 }
  securityGroupSelectorTerms:
    - tags: { karpenter.sh/discovery: distai-eks-0807 }
  tags: { Environment: dev, ManagedBy: karpenter-tenant-pools }
  tenants:
    defaultPolicy: Deny
    entries:
      - namespaces: ["team-gpu"]
        maxPools: 4
        capacityReservationIDs: ["cr-0056555dd93a28dde"]
EOF
```

次にテナントが CB を参照するプールを作ります。Capacity Block は単一 AZ なので、`zones` を予約の AZ 1 つに絞ります（EFA を使うプールや CB を参照するプールは単一 AZ に解決される必要があります）。

```bash
k apply -f - <<'EOF'
apiVersion: tenantpools.dev/v1alpha1
kind: AcceleratorPool
metadata:
  name: p5-cb
  namespace: team-gpu
spec:
  classRef: distai-default
  accelerator: { kind: nvidia }
  instances:
    families: ["p5"]
    capacityTypes: ["reserved"]
    zones: ["us-east-2a"]
  capacityReservationIDs: ["cr-0056555dd93a28dde"]
  limits:
    nvidia.com/gpu: "8"
EOF

k get acceleratorpool -n team-gpu p5-cb -o wide
```

実機出力:

```text
NAME    KIND     CLASS            READY   NODEPOOL                          AGE
p5-cb   nvidia   distai-default   True    team-gpu-p5-cb-c93c47721822b8e7   8s
```

条件（conditions）も確認します。

```bash
k get acceleratorpool -n team-gpu p5-cb \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status} ({.reason}){"\n"}{end}'
```

実機出力:

```text
Validated=True (ValidationSucceeded)
Ready=True (Ready)
```

生成された EC2NodeClass には、Karpenter の `capacityReservationSelectorTerms` として CB の ID が入り、NodePool の requirements には `reserved` と AZ の制約が載ります。

```bash
k get ec2nodeclass team-gpu-p5-cb-c93c47721822b8e7 \
  -o jsonpath='{.spec.capacityReservationSelectorTerms}{"\n"}'
k get nodepool team-gpu-p5-cb-c93c47721822b8e7 \
  -o jsonpath='{range .spec.template.spec.requirements[*]}{.key}={.values}{"\n"}{end}'
```

実機出力:

```text
[{"id":"cr-0056555dd93a28dde"}]
karpenter.k8s.aws/instance-gpu-manufacturer=[nvidia]
karpenter.k8s.aws/instance-family=[p5]
karpenter.sh/capacity-type=[reserved]
topology.kubernetes.io/zone=[us-east-2a]
```

Basic05 では `cb_reservation_id` を Terraform の tfvars に書いて apply していました。operator 版では、CB の ID は管理者が Class の allowlist に載せ、テナントがプールから参照する形になります。CB の購入自体は Basic05 と同じ手順で、operator は購入には関与しません。

## 6. テナント境界を確認する

ここで試すのは他テナントの toleration と未許可の Capacity Block ですが、VAP が閉じている経路はそれだけではありません。テナントのラベルを狙う `nodeSelector`、`nodeAffinity` の一部、そして `spec.nodeName` の直接指定も拒否されます。`nodeAffinity` について見ているのは `requiredDuringSchedulingIgnoredDuringExecution` の値だけなので、`preferred` 側や `Exists` や `NotIn` のように値だけでは意味が決まらない条件は、この検査では捕まりません (そこは taint による分離が受け持ちます)。特に `spec.nodeName` はスケジューラを丸ごと飛ばすので、taint による分離が働きません。デバッグのために `nodeName` を書いた既存の Pod がここで落ちるのは、この経路を閉じているためです。

境界が実際に効いていることを確かめます。まず、別テナントの taint を tolerate する Pod を `team-gpu` に作ろうとすると、VAP が admission の段階で拒否します。

```bash
k apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: cross-tenant
  namespace: team-gpu
spec:
  tolerations:
    - key: tenantpools.dev/tenant
      operator: Equal
      value: some-other-team
      effect: NoSchedule
  containers:
    - name: c
      image: public.ecr.aws/eks-distro/kubernetes/pause:latest
EOF
```

実機出力:

```text
Error from server (Forbidden): error when creating "STDIN": pods "cross-tenant" is
forbidden: ValidatingAdmissionPolicy 'ktp-karpenter-tenant-pools-pod-boundary' with
binding 'ktp-karpenter-tenant-pools-pod-boundary' denied request: pod may only tolerate
its own tenant taint (tenantpools.dev/tenant=team-gpu); wildcard or cross-tenant
tolerations are rejected
```

一方、自分のテナント値（`team-gpu`）を tolerate する Pod は通ります（`--dry-run=server` で admission だけを確認します）。

```bash
k apply --dry-run=server -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: own-tenant
  namespace: team-gpu
spec:
  tolerations:
    - key: tenantpools.dev/tenant
      operator: Equal
      value: team-gpu
      effect: NoSchedule
  containers:
    - name: c
      image: public.ecr.aws/eks-distro/kubernetes/pause:latest
EOF
```

実機出力:

```text
pod/own-tenant created (server dry run)
```

次に、Class の allowlist に無い CB を参照するプールを作ると、operator はそのプールを `Validated=False` のままにし、NodePool を生成しません。

```bash
k apply -f - <<'EOF'
apiVersion: tenantpools.dev/v1alpha1
kind: AcceleratorPool
metadata:
  name: p5-unauth
  namespace: team-gpu
spec:
  classRef: distai-default
  accelerator: { kind: nvidia }
  instances:
    families: ["p5"]
    capacityTypes: ["reserved"]
    zones: ["us-east-2a"]
  capacityReservationIDs: ["cr-0000000000deadbeef"]
  limits: { nvidia.com/gpu: "8" }
EOF

k get acceleratorpool -n team-gpu p5-unauth \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status} ({.reason}){"\n"}{end}'
k get nodepool -l tenantpools.dev/owner-name=p5-unauth
```

実機出力:

```text
Validated=False (ReservationNotAllowed)
Ready=False (ReservationNotAllowed)
No resources found
```

taint（スケジューラ）、VAP（admission）、CB allowlist（reconcile）の 3 つがそろって、テナント境界が仕組み上迂回できない形で守られていることが確認できました。ここで前提になっているのは、VAP を有効にした既定構成であることと、除外ラベルが付いていない namespace であることです。VAP を切れば admission の層が抜け、Pod 側は taint だけで守ることになります。

:::message alert
このプールで実際に GPU ノードを起動する前に、device plugin がテナント taint を tolerate できるかを必ず確認してください。operator が生成する NodePool には `tenantpools.dev/tenant=<namespace>` の taint が載りますが、Basic04 で導入した NVIDIA device plugin（GPU Operator）の DaemonSet は、既定では `nvidia.com/gpu` などを tolerate するだけで、このテナント taint を tolerate しません。実機の DaemonSet の toleration を確認すると、次のようにテナント taint が含まれていないことが分かります。

```bash
k get ds -n gpu-operator nvidia-device-plugin-daemonset \
  -o jsonpath='{range .spec.template.spec.tolerations[*]}{.key}{" "}{.operator}{" "}{.effect}{"\n"}{end}'
```

実機出力:

```text
nvidia.com/gpu Exists NoSchedule
vpc.amazonaws.com/efa Exists NoSchedule
capacity-reservation Exists NoSchedule
```

この状態でテナント taint 付きのノードを起動すると、device plugin の Pod がそのノードに載れず、GPU が advertise されないため、GPU を要求するワークロードは永久に `Pending` になります。device plugin にはテナント taint を tolerate させる必要があります。ただし `operator: Exists` のワイルドカードを足すのは行き止まりです。VAP は「テナントキーに触る toleration は `operator: Equal` かつ値が自分の namespace と一致するものだけ」を通すので、ワイルドカードは VAP 自身に `Forbidden` で拒否されます。DaemonSet の更新自体は通っても、そこから生まれる Pod の作成が弾かれ、症状は変わりません。単一の DaemonSet に全テナントの値を `Equal` で列挙するのも現実的ではありません。

実装が用意している逃げ道は namespace 単位の除外です。device plugin が動く namespace (GPU Operator なら `gpu-operator`) に `tenantpools.dev/excluded=true` ラベルを付けると、その namespace の Pod は VAP の対象から外れ、ワイルドカード toleration を持てるようになります。既定で名前指定で除外されているのは `kube-system`、`kube-node-lease`、`kube-public` の 3 つだけなので、それ以外の namespace で動く device plugin には自分でこのラベルを付けます。除外した namespace はテナント境界の外側になるので、`kubectl get ns -l tenantpools.dev/excluded` で誰が除外されているかを追えるようにしてあります。
:::

## 7. 後始末をする

作成した CR を削除します。`AcceleratorPool` を消すと、operator が生成した NodePool / EC2NodeClass も順序立てて削除されます。ノードを起動していなければ On-Demand の課金は発生していません (購入済みの Capacity Block の予約料金は、ノードを起動したかどうかとは別に発生します)。

```bash
k delete acceleratorpool -n team-gpu g5 p5-cb p5-unauth --ignore-not-found
k get nodepool -l app.kubernetes.io/managed-by=karpenter-tenant-pools | grep team-gpu || echo "(no team-gpu nodepools remain)"
k delete acceleratorclass distai-default --ignore-not-found
k delete namespace team-gpu --ignore-not-found
```

operator 自体を外す場合は `helm uninstall ktp -n tenantpools-system` を実行します。このチャートは CRD を `crds.install` トグル付きの通常のテンプレートとして持っているので、`helm uninstall` で CRD も消えます。CRD が消えると、それに属する `AcceleratorPool` と `AcceleratorClass` もカスケードで消えます。プール定義を残したい場合は先に `k get acceleratorpool -A -o yaml` で退避してから uninstall してください。

# まとめ

本章では、Basic04 と Basic05 で Terraform の共有変数として書いていた g5 の On-Demand プールと p5 の Capacity Block プールを、`karpenter-tenant-pools` の CRD から立ち上げました。管理者は `AcceleratorClass` にロール・AMI・セレクタ・テナント allowlist を集約し、テナントは自分の namespace の `AcceleratorPool` に「欲しいプールの要望」だけを書きます。生成される NodePool / EC2NodeClass は Basic04 の Terraform 版とほぼ同じ形になり、`READY=True` で Karpenter に受理されました。

テナント境界は 3 つの経路で仕組み上迂回できない形で守られます。生成 NodePool には namespace から導出したテナント taint が必ず載り、同梱の VAP が別テナントやワイルドカードの toleration を持つ Pod を admission で拒否し、Capacity Block へのアクセスは管理者が Class の allowlist に載せた場合にだけ許可されます。allowlist に無い CB を参照したプールは `Validated=False` に留まり NodePool を生成しない、という挙動も実機で確認しました。

operator は AWS を一切呼ばず、EC2 の起動は Karpenter に委ねます。そのためクラウドの認証情報を 1 つも必要としません。チャートには Capacity Block の実在確認のために `ec2:DescribeCapacityReservations` を読むための `awsLookup` という値が用意されていますが、現時点の Deployment はこの値を operator に渡していないので、有効にしても挙動は変わりません。したがって Karpenter・device plugin・Capacity Block の購入は引き続き Basic シリーズの手順で行い、operator はあくまで「プール定義の入り口」を集中管理から namespace 単位のセルフサービスに置き換える層として機能します。単一チームで運用する分には Basic04 の Terraform 版で十分ですが、複数チームが独立にプールを足す運用に踏み込むなら、この構築的な境界が効いてきます。

# 参考資料

- [karpenter-tenant-pools](https://github.com/littlemex/karpenter-tenant-pools)
- [Karpenter NodePools](https://karpenter.sh/docs/concepts/nodepools/)
- [ValidatingAdmissionPolicy](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/)
- [Amazon EC2 Capacity Blocks for ML](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-blocks-using.html)
- [対象モジュール infra/eks](https://github.com/littlemex/distributed-ai/tree/main/infra/eks)
