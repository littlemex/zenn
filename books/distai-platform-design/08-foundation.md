---
title: "セルの Terraform 基盤層を作る"
---

## はじめに

[littlemex/distai-platform](https://github.com/littlemex/distai-platform) は、GPU と Trainium を同じ形で扱う EKS 基盤を、Issue から PR までの手順で育てているリポジトリである。この章で扱うのは、そのリポジトリに最初に入った実装である [`infra/foundation/`](https://github.com/littlemex/distai-platform/blob/db7812c1ae9eb53b7474c160e3c69c7444ea97c3/infra/foundation)(Issue [#20](https://github.com/littlemex/distai-platform/issues/20)、PR [#21](https://github.com/littlemex/distai-platform/pull/21)、本章執筆時点の head は `db7812c`)である。この層は、1 つのセル(クラスタ 1 個分の環境)の入れ物だけを Terraform で作る。何を作り、何を作らないかの線引き、10 個の変数と 11 個の出力からなるインターフェース、削除の順番を守るための仕掛け、複数チームが 1 つのセルを共有する運用に向けてこの層が足した下ごしらえ、そして実機で重ねた e2e テストの結果までを、この章で順に見ていく。

この章を読むと、次の問いに答えられるようになる。1 つのセルのうち、どこまでを Terraform の state に入れ、どこから Argo CD に渡すのか。公開エンドポイントや管理者をどう絞るのか。Karpenter 本体を Terraform で入れないのに、Karpenter が起動した EC2 インスタンスをどうやって安全に壊すのか。複数チームが 1 つのセルを共有するとき、この層がすでに保証していることと、まだテナンシー層([Issue #22](https://github.com/littlemex/distai-platform/issues/22))に残っていることは何か。

## 1. 基盤層が作るもの、作らないもの

1 つのセルには、クラスタが生きている間ずっと変わらないもの(ネットワーク、クラスタ本体、最低限のノード)と、毎日のように変わるもの(どの NodePool を使うか、どの Capacity Block を買うか、どの device plugin を入れるか)が混ざっている。両方を 1 つの Terraform の state に入れると、Capacity Block を 1 つ買うだけで、巨大な state の plan を見ることになる。

`infra/foundation` が受け持つのは前者だけで、後者は Argo CD(Issue [#17](https://github.com/littlemex/distai-platform/issues/17) で決めた設計)に渡す。1 つのセルに 1 つの Terraform state という単位も、ここで固定される。

<!-- FIG name="08-scope" kind="relation" shows="Terraform が作る範囲(左)と Argo CD が作る範囲(右)の境界、つながるのは出力だけ" labels="infra/foundation, VPC, EKS cluster, system node group, Karpenter prerequisites, teardown guard, Argo CD, NodePool, Kueue, device plugin, DRA driver, outputs only" -->
![Terraform と Argo CD が作る範囲の境界を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/08-scope.png)

作るものと作らないものを並べると、次のようになる。

| 作る | 作らない |
| --- | --- |
| VPC(リージョンの全 Availability Zone にパブリックとプライベートのサブネット)、NAT ゲートウェイ、Karpenter の discovery タグ | Karpenter 本体、NodePool、EC2NodeClass |
| EKS 1.37 クラスタ(`authentication_mode = "API"`)、管理者のアクセスエントリ | Kueue、JobSet、Trainer、LeaderWorkerSet |
| システム用のマネージドノードグループ(既定 `m7i.large` を 2 台、AL2023、`CriticalAddonsOnly=true:NoSchedule` の taint、IMDSv2 必須・hop limit 1 の起動テンプレート) | device plugin、DRA ドライバ |
| EKS マネージド add-on(`vpc-cni`、`kube-proxy`、`eks-pod-identity-agent`、`coredns`)、バージョンをピン留め。`vpc-cni` は `kube-system/aws-node` 専用の Pod Identity ロールで動き、`enableNetworkPolicy` を有効化する | 容量の予約(ODCR、Capacity Block)、`NetworkPolicy` オブジェクト本体 |
| Karpenter の前提(Pod Identity を結んだコントローラロール、ノードロール、中断通知の SQS キューと EventBridge のルール) | 可観測性のスタック |
| 任意の、マネージドの Argo CD capability(`ADMIN`/`EDITOR`/`VIEWER` の `role_mappings`) | チームごとの AppProject、Karpenter が動いていることを前提にするもの全部 |
| 削除ガード(ネットワークを壊す前に Karpenter のインスタンスを片付ける) | — |

このうち、IMDSv2 の起動テンプレート、ノードロールからの CNI 権限の分離、`NetworkPolicy` の施行の有効化、Argo CD の `role_mappings` の 4 点は、1 つのセルを複数チームで共有する運用([Issue #22](https://github.com/littlemex/distai-platform/issues/22) で設計中のテナンシー層)に向けてこの層が足した下ごしらえである。それぞれの理由とやり方は 4 節、5 節、7 節、8 節で順に見る。

## 2. インターフェース: 10 個の変数と 11 個の出力

後の層(Argo CD や、これから作る層)はこの層の出力だけを読む。出力の名前を変えることは破壊的な変更になるので、インターフェースはこの章の中で固定して示す。

変数は 10 個で、それぞれに `description`、`type`、必要なら `validation` がある。

| 変数 | 型 | 既定値 | 意味 |
| --- | --- | --- | --- |
| `name` | `string` | (必須) | セルとクラスタの名前。小文字 3〜32 文字、先頭は文字、ハイフンで終わらない |
| `region` | `string` | (必須) | AWS リージョン |
| `kubernetes_version` | `string` | `"1.37"` | EKS のバージョン。`eks.tf` の `local.addon_versions` に対応するエントリが無いバージョンは plan 時に拒否される |
| `vpc_cidr` | `string` | `"10.0.0.0/16"` | VPC の CIDR。`/16` から `/20` までの範囲 |
| `single_nat_gateway` | `bool` | `true` | VPC 全体で NAT ゲートウェイを 1 個にする(検証用のセル向け)か、Availability Zone ごとに 1 個にするか |
| `endpoint_public_access_cidrs` | `list(string)` | (必須) | 公開 API エンドポイントに到達できる CIDR のリスト。空リストは許されず、各エントリは `/8` 以上のネットワークアドレスでなければならない |
| `admin_principal_arns` | `list(string)` | `[]` | アクセスエントリを通じて `AmazonEKSClusterAdminPolicy` を得る IAM ロールかユーザーの ARN。STS で assume したロールの ARN(`sts:AssumeRole` の結果)は拒否され、重複も許されない |
| `system_node_group` | `object({ instance_types, size })` | `{}` | システムノードグループ。`instance_types` の既定は `["m7i.large"]`(1 件以上必須)、`size` の既定は 2(1 以上の整数、上限なし)。x86_64 のインスタンスタイプのみ(AMI タイプは `AL2023_x86_64_STANDARD`。これは README の説明であり、インスタンスタイプそのものが x86_64 かどうかを検証するコードは無い) |
| `argocd` | `object({ idc_instance_arn, idc_region, role_mappings })` | `null` | 設定するとマネージドの Argo CD capability を作る。`role_mappings` は `role`(`ADMIN`/`EDITOR`/`VIEWER`、同じロールを 2 回使うことはできない)と `group_ids`(1 件以上)の組を列挙する |
| `tags` | `map(string)` | `{}` | この層が作るすべてのリソースに付けるタグ |

出力は 11 個で、後の層がそれぞれどこで使うかも決まっている。

| 出力 | 典型的な使い先 |
| --- | --- |
| `cluster_name` | Karpenter の Helm 値 `settings.clusterName` |
| `cluster_endpoint` | 後の層の Kubernetes クライアント |
| `cluster_certificate_authority_data` | 後の層の Kubernetes クライアント |
| `region` | 後の層 |
| `vpc_id` | 後の層 |
| `private_subnet_ids` | ロードバランサー、ストレージ |
| `node_security_group_id` | ストレージとロードバランサーのルール |
| `karpenter_controller_role_arn` | 参照のみ(Pod Identity の関連付けはこの層で既に済んでいる) |
| `karpenter_node_role_name` | EC2NodeClass の `spec.role` |
| `karpenter_interruption_queue_name` | Karpenter の Helm 値 `settings.interruptionQueue` |
| `argocd_capability_arn` | 作らなかったときは `null` |

<!-- FIG name="08-interface" kind="relation" shows="10個の変数が foundation layer に入り、11個の出力が後続の層(Karpenter Helm, EC2NodeClass など)に渡る" labels="variables, infra/foundation, outputs, cluster_name, karpenter_node_role_name, karpenter_interruption_queue_name, argocd_capability_arn" -->
![変数と出力のインターフェースを示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/08-interface.png)

EC2NodeClass は、`subnetSelectorTerms` と `securityGroupSelectorTerms` を、この層が付けたタグ `karpenter.sh/discovery: <cluster_name>` で選ぶ。Karpenter はこの層が作ったコントローラロールを受け取るために、`kube-system` の namespace で、サービスアカウント名 `karpenter` として動く必要がある(このサービスアカウント名は、Helm chart を `kube-system` に入れたときの既定の値である)。

## 3. ネットワーク構成

この層のネットワークは、Capacity Block や spot の容量がどの Availability Zone で提供されても Karpenter がノードを置けるように、リージョンの全 Availability Zone にプライベートサブネットを用意する。そのために、VPC の CIDR は 8 個のブロックに割る。ブロック 0 から 6 がプライベートサブネット(Availability Zone ごとに 1 個、既定の `/16` なら `/19` が 1 つあたり 8,190 アドレス)、ブロック 7 をさらに 32 分割したものがパブリックサブネット(`/24`)である。パブリックサブネットに乗るのは NAT ゲートウェイとロードバランサーだけで、インスタンスにパブリック IP は割り当てない。

コントロールプレーンとシステムノードグループは、EKS がコントロールプレーンに使えないと定めている Availability Zone(`use1-az3`、`usw1-az2`、`cac1-az3`)を避けたプライベートサブネットを使う。Karpenter はどの Availability Zone でも使ってよい。これは、Capacity Block や spot の容量が Availability Zone 単位で提供されるため、予約を買う前に全ての Availability Zone にサブネットが無ければならないという理由による。

`single_nat_gateway` の既定は `true` で、VPC 全体で NAT ゲートウェイを 1 個にする。これは検証用のセルに向いた選択であり、長く動かすセルでは `single_nat_gateway = false` にして、NAT ゲートウェイが単一障害点になることを避ける必要がある。デフォルトのセキュリティグループは全てのルールを空にして、誤って使われないようにしている。

<!-- FIG name="08-network" kind="position" shows="VPCのCIDR分割、AZごとのpublic/privateサブネット配置、NATゲートウェイの位置" labels="VPC, public subnet, private subnet, NAT gateway, Availability Zone, karpenter.sh/discovery" -->
![VPC のサブネット配置を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/08-network.png)

ノードが使うセキュリティグループは、別に作らず、EKS が作るクラスタセキュリティグループ(コントロールプレーンと全ノードで共有)をそのまま使う。Karpenter はこのセキュリティグループを、discovery タグ(`karpenter.sh/discovery`)で選ぶ。

## 4. 公開エンドポイントと管理者の絞り方

この節の設定は、API サーバーに到達できる範囲と管理者になれる主体を変数に書いたものだけに絞り、あわせて中断通知のキュー、IAM ロール、ノードの資格情報が想定外の主体に使われる経路を塞ぐ。この PR のレビューで指摘された項目の半分近くは、セキュリティに関わるものだった。選んだことと、それぞれが何を防ぐかを並べると、次のようになる。

| 選んだこと | 防いだこと |
| --- | --- |
| 公開エンドポイントの CIDR は `/8` 以上に限定(空リスト、`0.0.0.0/0`、`/1` を 2 本組み合わせた同じ広さも拒否) | 誰でも API サーバーに到達できる状態 |
| 管理者はアクセスエントリに書いた ARN だけ(`bootstrap_cluster_creator_admin_permissions = false`) | 変数に出ない「隠れた管理者」 |
| SQS キューは自分の EventBridge のルールの ARN からだけ送信を許可 | このセルの EventBridge のルール以外から偽の中断通知を送れる状態(同じアカウント内で `sqs:SendMessage` の IAM 権限を持つ別の主体は、この制限の対象外である) |
| Karpenter コントローラロールの trust policy は Pod Identity のセッションタグ 3 つも要求 | 同じアカウントの別の Pod がロールを乗っ取る状態 |
| 削除ガードは実行前に呼び出し元のアカウント ID を確認 | 別アカウントの認証情報で「何も消さず PASS」になる事故 |
| システムノードグループに専用の起動テンプレートを持たせ、IMDSv2 必須・`http_put_response_hop_limit = 1` を強制 | ホストネットワークを使わない Pod が IMDS からノードロールの資格情報を得る状態 |
| ノードロールから `AmazonEKS_CNI_Policy` を外し、`kube-system/aws-node` に Pod Identity で結んだ専用ロールだけに持たせる | ノードロールの資格情報を得た Pod が ENI を作成・変更・削除できる状態 |

EKS は、公開エンドポイントの CIDR リストが空だと「制限なし」と解釈する。そこで `endpoint_public_access_cidrs` は必須の変数にし、さらに `/8` より広い範囲を拒否する検証を `variables.tf` に 3 つ重ねている。1 つ目は空リストでないこと、2 つ目は各エントリが有効な IPv4 の CIDR であること、3 つ目は各エントリが `/8` 以上のネットワークアドレスであること(`cidrsubnet(c, 0, 0) == c` でホスト部が 0 であることを確かめる)である。これで `0.0.0.0/0` だけでなく、同じ広さを作る別の書き方(`/1` を 2 本組み合わせるなど)も拒否できる。

管理者の設定は `authentication_mode = "API"`、`bootstrap_cluster_creator_admin_permissions = false` にしたうえで、`admin_principal_arns` に列挙した ARN ごとに `AmazonEKSClusterAdminPolicy` を持つアクセスエントリを作る。Terraform を実行した本人の権限を暗黙に残すと、どの変数にも現れない管理者ができてしまうので、それを避けている。`admin_principal_arns` が空のままだと、エントリが追加されるまで誰も `kubectl` で入れないクラスタになる。

表の最後の 2 行(IMDSv2 の強制と CNI 権限の分離)は、1 つのセルを複数チームで共有する運用([Issue #22](https://github.com/littlemex/distai-platform/issues/22) で設計中のテナンシー層)に向けて、この PR の 17 件のレビュー指摘とは別に、Issue #20 への追記としてこの層が後から足したものである。`CriticalAddonsOnly` の taint だけでは、どの Pod でもこの taint を tolerate できてしまうため、チームの Pod をシステムノードから締め出すことはできない。締め出す仕組み自体(admission policy)はテナンシー層の仕事だが、締め出しに失敗した Pod がシステムノードに乗れたとして、そこで何を得られるかをこの層が削っておく、という役割分担である。

IMDSv2 の強制は、システムノードグループに `image_id` も `instance_type` も指定しない `aws_launch_template` を 1 つ作り、`metadata_options` だけを設定して、ノードグループ本体にアタッチする形で実装している。`ami_type` と `instance_types` はノードグループ側に残したままなので、EKS は AL2023 の nodeadm ブートストラップ用のユーザーデータをこれまでと同じように生成する。`http_put_response_hop_limit = 1` にすると、ホストネットワークを使わない Pod は Pod のネットワーク名前空間からノードに入るまでに 2 ホップ以上かかるため、IMDSv2 のトークンを一切取得できない。`aws-node` や kubelet のようにホストネットワークを使うプロセスは 1 ホップなので影響を受けない。この変更には副作用があり、Karpenter のコントローラ自身も `CriticalAddonsOnly` を tolerate してシステムノード上で動くため、hop limit 1 の後は自分のリージョンを IMDS から自動検出できなくなる。そのため e2e のテストでは、Karpenter の Helm values の `controller.env` に `AWS_REGION` を明示的に渡すように変えている。

CNI 権限の分離は、`vpc-cni` の add-on に EKS Pod Identity の関連付け(`pod_identity_association`、`kube-system`/`aws-node` 向け)を持たせ、`AmazonEKS_CNI_Policy` をそのロールだけにアタッチする形で実装している。このロールの trust policy は、Karpenter コントローラロールと同じパターンで、`aws:SourceAccount` に加えて EKS Pod Identity が自動で付ける `eks-cluster-arn`・`kubernetes-namespace`・`kubernetes-service-account` のセッションタグを要求する。システムノードロールと Karpenter のノードロールには、残る `AmazonEKSWorkerNodePolicy`、`AmazonEC2ContainerRegistryPullOnly`、`AmazonSSMManagedInstanceCore` だけが付く。代案としては、CNI 権限をそのままノードロールに残して taint だけに頼る案(EKS の既定の形だが、taint を 1 つ tolerate するだけでネットワーク制御まで手が届いてしまう)と、Pod Identity の代わりに `aws-node` にも IRSA を使う案(クラスタごとに OIDC プロバイダが要り、Karpenter が使っているのとは別の認証の仕組みを並走させることになる)を検討したが、どちらも採らなかった。

## 5. managed add-on のピン留めと順序

この層は、クラスタの add-on をすべて EKS マネージド add-on として管理下に置き、バージョンを固定したうえで、ノードが Ready になれる順序で入れる。そのために、クラスタを `bootstrap_self_managed_addons = false` で作る。add-on には 2 種類あり、`kube-proxy`、`eks-pod-identity-agent`、`vpc-cni` のような DaemonSet 系はシステムノードグループより先に入れないとノードが Ready にならない。一方で `coredns` のような Deployment はノードが無いとスケジュールできないので後に入れる(`eks.tf` の `addons_before_nodes` / `addons_after_nodes`)。

ただし `vpc-cni` だけは `addons_before_nodes` のセットには入っておらず、`eks-pod-identity-agent` が `ACTIVE` になった後に別のリソースとして作る。理由は、`aws-node`(`vpc-cni` の DaemonSet)が自分の AWS 資格情報を EKS Pod Identity から取るようになった(前節)ことである。新しくクラスタに参加するノードでは、`aws-node` と Pod Identity エージェントの DaemonSet がほぼ同じタイミングで起動しようとするので、エージェント側の DaemonSet オブジェクトが先にクラスタへ届いていなければ、ノードが `aws-node` をエージェントより先に起動させてしまう競合が起こりうる。`aws_eks_addon.vpc_cni` に `eks-pod-identity-agent` への `depends_on` を持たせて、エージェントが `ACTIVE` になった後に初めて `vpc-cni` を作ることで、これより後に参加するすべてのノード(システムノードグループも、Karpenter が起動するノードも)にとって、エージェントの DaemonSet オブジェクトが既にクラスタに存在する状態を作ろうとしている。この競合自体は実機で観測されたものではなく、順序の無い旧バージョンのコードから原理的に起こり得ると判断して直したものであり、この `depends_on` だけで十分かは、レビューでまだ結論が出ていない論点として残っている。

<!-- FIG name="08-addon-order" kind="flow" shows="add-onの作成順序とそれぞれの依存関係、vpc-cniがeks-pod-identity-agentのACTIVEに依存する理由" labels="kube-proxy, eks-pod-identity-agent, vpc-cni, Pod Identity association, aws-node, system node group, coredns" -->
![add-on の作成順序を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/08-addon-order.png)

`vpc-cni` の `configuration_values` には `enableNetworkPolicy = "true"` も渡している。これは `NetworkPolicy` の施行そのものを有効にするだけで、施行対象になる `NetworkPolicy` オブジェクトはこの層では 1 つも作らない。ポリシーが無い間は通信の振る舞いは何も変わらず、オブジェクトを足す側のレイヤー(テナンシー層、Issue #22)が足した瞬間からそれが効くようにするための下ごしらえである。

バージョンは Kubernetes のバージョンごとに `local.addon_versions` でピン留めする。実機の e2e で確認されたバージョンは次のとおりである。

| add-on | バージョン |
| --- | --- |
| `vpc-cni` | `v1.23.3-eksbuild.1` |
| `kube-proxy` | `v1.37.0-eksbuild.39` |
| `eks-pod-identity-agent` | `v1.4.1-eksbuild.3` |
| `coredns` | `v1.14.7-eksbuild.35` |

`most_recent = true` にして自動で最新を追う案もあったが、同じコードが日によって違うバージョンを適用することになるため採らなかった。バージョンの変更はレビューされる diff にする、という理由である。

Karpenter 自体はここでは入れない(Argo CD が入れる)が、Karpenter が必要とする AWS 側の下ごしらえはこの層で作る。IAM は、Karpenter バージョン 1.15 のリファレンスの CloudFormation テンプレートが持つコントローラのポリシーを、6 つのカスタマー管理ポリシーに分けて実装している(`node-lifecycle`、`iam-integration`、`eks-integration`、`interruption`、`zonal-shift`、`resource-discovery`)。コントローラロールは EKS Pod Identity を通じて `kube-system/karpenter` に関連付けられ、ノードロールには `EC2_LINUX` のアクセスエントリが付く。

中断通知の SQS キューは、サーバー側の暗号化を有効にし、TLS 以外からの操作を拒否する deny 文を持ち、`sqs:SendMessage` はこのセルの EventBridge のルールの ARN(`aws:SourceArn`)からだけ許可する。Karpenter のリファレンステンプレートは `sqs.amazonaws.com` というサービスプリンシパルからの送信も許しているが、この層ではそれを落としている。この点がアップストリームのテンプレートより厳しい差である。

EventBridge のルールは 5 本あり、4 本(`scheduled-change`、`spot-interruption`、`rebalance`、`instance-state-change`)はよくあるテンプレートどおりだが、5 本目の `capacity-reservation`(`EC2 Capacity Reservation Instance Interruption Warning`)は Issue #20 で挙げられていた 4 本に追加したものである。3 章で見た `-rsv` プールは ODCR と Capacity Block の上で動くため、Karpenter がこの警告を受け取らなければそれらのノードを排出できない、という理由による。

## 6. 削除の順番と削除ガード(teardown guard)

Karpenter が起動した EC2 インスタンスは、Karpenter 自身が Argo CD 経由で入るので、この Terraform の state には記録が無い。`terraform destroy` が素直にネットワークから壊していくと、インスタンスはまだ動いているのにサブネットや NAT ゲートウェイを先に消すことになる。すると、インスタンスのネットワークインターフェース(ENI)が邪魔をして destroy が止まるか、NAT ゲートウェイを先に消したことで Karpenter のノードが AWS への経路を失い、インスタンスを片付けられないまま残る。これは仮説ではなく、以前の実験([Issue #19](https://github.com/littlemex/distai-platform/issues/19) の E0 から E3 でのクラスタ解体)で実際に起きた失敗であり、手順書に順番を書くだけの対処も、そのときすでに 1 度失敗している。

<!-- FIG name="08-teardown" kind="flow" shows="先にNATを壊すと起きる失敗(左)と、teardown guardが強制する正しい順番(右)" labels="NAT gateway, Karpenter instance, ENI, teardown-guard.sh, terraform_data, destroy" -->
![teardown guard が強制する削除順序を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/08-teardown.png)

これを避けるため、[`teardown.tf`](https://github.com/littlemex/distai-platform/blob/db7812c1ae9eb53b7474c160e3c69c7444ea97c3/infra/foundation/teardown.tf) の `terraform_data` が、destroy 専用の `local-exec` プロビジョナーで [`teardown-guard.sh`](https://github.com/littlemex/distai-platform/blob/db7812c1ae9eb53b7474c160e3c69c7444ea97c3/infra/foundation/scripts/teardown-guard.sh) を呼ぶ。destroy の順番は次のようになる。

1. Karpenter コントローラロールのポリシーの attach は、この `terraform_data` に `depends_on` しているため先に destroy され、Karpenter は新しいインスタンスを起動できなくなる。
2. `teardown-guard.sh` が走る。
3. この削除ガード自身が、クラスタ、ノードロール、ネットワークのリソース全部に `depends_on` しているので、それらはこの削除ガードが終わるまで destroy されない。

削除ガードはまず、自分を動かしている AWS CLI の呼び出し元アカウントと、Terraform が対象にしているアカウントが一致することを確認する。これが無いと、プロバイダが別のロールを assume していたときに、呼び出しが全部「何も無い」と返ってきて、何も削除せずに PASS してしまう。

確認が通ったら、次の順で片付ける。

1. **インスタンス**: タグ `eks:eks-cluster-name = <name>` と `karpenter.sh/nodepool` を持つインスタンスを `terminate-instances` で終了し、`instance-terminated` の waiter で待つ。これを最大 5 ラウンド繰り返す。インスタンスが「無い」ことは 15 秒空けて 2 回確認する。ちょうどコントローラの権限が切れる直前に起動が始まったインスタンスが、少し遅れて見えてくることがあるためである。
2. **ネットワークインターフェース**: VPC CNI が付けた、説明が `aws-K8S-*` で始まる、どのインスタンスにも紐付いていない ENI を削除する。
3. **起動テンプレート**: タグ `eks:eks-cluster-name = <name>` とタグキー `karpenter.k8s.aws/ec2nodeclass` の両方を持つ起動テンプレートを削除する(当初は Karpenter v1 が付けない別のタグキーで絞り込んでいたため常に 0 件になり、9 節の 6 回目と 7 回目の e2e で見つけて直した)。
4. **インスタンスプロファイル**: ノードロールにぶら下がるインスタンスプロファイルのうち、Karpenter 自身が付けるタグ `kubernetes.io/cluster/<name> = owned` を持つものだけを削除する。他の用途のプロファイルを誤って消さないための確認である。

削除の呼び出しは、対象が既に無くなっていても(VPC CNI や Karpenter が同時に消すことがある)失敗にしない `tolerate_not_found` の仕組みを通す。削除ガードが失敗すると、`terraform destroy` はネットワークのリソースを消す前に止まる。

削除ガードが消すのは、Karpenter が AWS に残すものだけである。`Service` や `Ingress` から作られるロードバランサー、`PersistentVolumeClaim` から作られる EBS ボリュームのような、Kubernetes オブジェクトから作られるリソースは、それらを作った層が、この基盤を壊す前に削除する責任を持つ。

この削除ガードには、まだ直っていない欠陥が 1 つ残っている([Issue #26](https://github.com/littlemex/distai-platform/issues/26))。削除ガードは `terraform_data.teardown_guard` という 1 つのリソースで、クラスタ、ノードグループ、ネットワークのリソース全部に自分から `depends_on` しているため、作成の順番としては最後に作られる。`terraform apply` がクラスタまで作った後、途中で一時的な AWS API のエラー(この Issue が報告された実際の run では EventBridge のルールを読む呼び出し)が起きて失敗すると、state には VPC やクラスタは残っているのに削除ガード自身は一度も作られていない。この状態で `terraform destroy` を実行しても、削除ガードが走らないので、EKS や VPC CNI が裏で作ったリソース(検出済みの出来事ではプライベートサブネットの ENI 2 個と、EKS が自動で作るクラスタのセキュリティグループ)が手つかずで残り、サブネットと VPC の削除が止まる。これは[Issue #25](https://github.com/littlemex/distai-platform/issues/25)(配送層、[5b 章](./05b-delivery-layer))の最初の PR の e2e で、基盤層の `terraform apply` が途中で失敗したときに見つかった欠陥であり、どの AWS API の一時的なエラーで途中失敗が起きても同じ形で現れる([5b 章](./05b-delivery-layer) 9 節で見る、同じ PR の検証の中のアップグレード e2e での、別の API 呼び出しの失敗も、同じガードの欠陥に当たっている)。直す案は 2 つ比べられている。

| 選択肢 | 何か | なぜ | 決定 |
| --- | --- | --- | --- |
| (a) 削除ガードの依存関係を逆にして、VPC の直後(クラスタより前)に作られるようにし、クラスタやノードグループの側を削除ガードに依存させる | 作られる順番が早くなり、途中失敗でもすでに存在している可能性が上がる | 正常な完全作成からの削除で「Karpenter の権限を先に消し、その後で削除ガードが動く」という既存の保証を、保ったまま直す必要がある | 未決定 |
| (b) 削除ガードの場所は変えずに、ネットワークのリソースの手前にもう 1 つ早期の destroy 専用ガード(このセルの ENI とクラスタセキュリティグループだけを消す小さなもの)を足す | 既存のガードの保証はそのまま残る | 2 つのガードを持つことになり、どちらが何を消すかの説明が増える | 未決定 |

この章を書いている時点では、どちらを採るかはまだ決まっていない。

## 7. Argo CD capability

Argo CD capability は、#17 の「セルの中身は全部 Argo CD から配る」という設計をこの層で実装する部分である。`argocd` 変数を設定すると、EKS のマネージドな Argo CD capability(`type = "ARGOCD"`)を作る。サインインは IAM Identity Center を通じ、`role_mappings` に列挙したエントリごとに、そのグループ群を Argo CD の `ADMIN`・`EDITOR`・`VIEWER` のいずれか 1 つのロールにマッピングする(1 つのロールは 1 エントリまでで、同じロールを 2 回使うことはできない)。もともとはグループが `admin_group_ids` としてまとめて `ADMIN` ロールにしかマッピングできなかったが、4 節で見たマルチテナンシーの追記で `role_mappings` に広げ、閲覧だけ、編集だけの権限も渡せるようにした。これはクラスタ全体に対するロールのままで、チームごとに 1 つのネームスペースへ絞った `AppProject` は作らない。そちらはテナンシー層([Issue #22](https://github.com/littlemex/distai-platform/issues/22))の仕事である。capability のロールには権限ポリシーを一切付けない。EKS が、capability 自身のクラスタへのアクセスをアクセスエントリで用意するためである。EKS のマネージドな機能を使うことで、Argo CD を自分で動かし、アップグレードする手間を無くしている。

当初、この分岐は実機で確認できていなかった。IAM Identity Center のインスタンスが要るため、9 節で見る run 1 から 7 までの e2e では `argocd` を未設定のままにし、`terraform fmt`・`validate`・`tflint` までしか通していなかった。Issue #20 の受け入れ基準は「`argocd` を設定すれば capability が `ACTIVE` になることを 1 回確認する」だったが、この基準はしばらく満たされないままだった。

これは、セル自体を `us-east-1`(IAM Identity Center のインスタンスが既に存在するリージョン)に構築し、専用の EC2 テストランナー上からその構築を実行することで解消した。`argocd` 変数に `ADMIN`・`EDITOR`・`VIEWER` の 3 ロールを渡すため、実行ごとに `aws identitystore create-group` で一時的なグループを 3 つ作り、`role_mappings` に渡して `terraform apply` し、確認が終わったら `aws identitystore delete-group` で消す、という手順を踏んでいる。この手順で実行した e2e は、Argo CD capability が `ACTIVE` になること、そして `role_mappings` に渡した `ADMIN`・`EDITOR`・`VIEWER` 各ロールのグループ ID が capability 自身の設定に反映されていることを、3 つとも実機で確認した。`argocd` は hub セル(複数のチームのセルを束ねる管理用のセル)専用の機能で、すべてのセルに必要なものではないが、capability が `ACTIVE` になることと `role_mappings` の設定への反映は、これで実機の裏付けを持つようになった。各ロールで実際にサインインできるかどうか、そのロールの権限が意図どおりに効くかどうかまでは、この run では確かめていない。この capability を「hub セル専用」と呼ぶ位置づけと、各セルが自分自身の capability を持って自分を登録する形という、[5b 章](./05b-delivery-layer) 1 節が指摘する食い違いも、まだ解消していない。

## 8. マルチテナンシーの下ごしらえ(Issue #22 に渡すもの)

[Issue #22](https://github.com/littlemex/distai-platform/issues/22) は、1 つのセルを複数チームで共有する運用(プラットフォーム管理者がセルを作り、各チームがその上で自分の学習・推論ワークロードを動かす形)を設計するテナンシー層の RFC で、まだ実装はなく、設計段階にある。その RFC 自身が、この基盤層(#20/#21)に対して次の 3 点を前提として明示している。`CriticalAddonsOnly` の taint だけではチームの Pod をシステムノードから締め出せないこと(どの Pod でもこの taint を tolerate できる)、実際の `NetworkPolicy` オブジェクトはテナンシー層自身が書くこと、チームごとの Argo CD `AppProject` もテナンシー層自身が作ることの 3 点である。そのうち最初の 1 つに対してこの層が応えたのが、4 節で見た IMDSv2 と CNI 権限の分離であり、残り 2 つについては、テナンシー層が後から積む本体(NetworkPolicy オブジェクト、AppProject)を受け止める仕掛け(NetworkPolicy の施行の有効化、Argo CD の `role_mappings`)だけをこの層が用意した。

| この層が保証すること(#20/#21) | テナンシー層([#22](https://github.com/littlemex/distai-platform/issues/22))に残ること |
| --- | --- |
| システムノードの IMDSv2・hop limit 1(4 節) | `CriticalAddonsOnly` とチームごとの taint を実際の境界にする admission policy |
| ノードロールに CNI 権限を持たせず、`aws-node` だけが Pod Identity で持つ(4 節、5 節) | チームごとのアクセスエントリ、チームの Pod 用の Pod Identity の関連付け |
| `vpc-cni` の `NetworkPolicy` 施行を有効化(5 節) | 実際の `NetworkPolicy` オブジェクトそのもの |
| Argo CD のクラスタ全体向け `role_mappings`(`ADMIN`/`EDITOR`/`VIEWER`、7 節) | チームごとの `AppProject` |

<!-- FIG name="08-tenancy" kind="relation" shows="この層が保証する3つのこと(IMDSv2、CNI権限の分離、NetworkPolicy施行)と、テナンシー層(#22)に残る3つのこと(admission policy、NetworkPolicyオブジェクト、AppProject)の境界" labels="IMDSv2, hop limit 1, aws-node, Pod Identity, AmazonEKS_CNI_Policy, enableNetworkPolicy, CriticalAddonsOnly, Issue #22, AppProject" -->
![この層とテナンシー層(#22)の境界を示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/08-tenancy.png)

Namespace、ResourceQuota、LocalQueue、Kueue の `Cohort`/`ClusterQueue` の配置、GPU と Neuron それぞれのデバイス共有のルール、ストレージとコストの配分の規約は、いずれもこの層には無く、#22 の範囲である。この層が増やしたのはあくまで、テナンシー層が後から積む仕掛けのための土台であり、土台だけでは複数チームを安全に分離できない。

9 節で見る e2e では、この層が足した 4 点(IMDSv2 の強制、CNI 権限の分離、`NetworkPolicy` の施行の有効化、Argo CD の `role_mappings`)のうち、実機で確かめられるものを確かめている。ホストネットワークを使わない Pod がシステムノード上で IMDSv2 のトークンを取得できないこと、システムノードの EC2 インスタンスが `HttpTokens = required` かつ `HttpPutResponseHopLimit = 1` であること、Karpenter のノードロールもシステムノードロールも `AmazonEKS_CNI_Policy` を持たないこと、`kube-system/aws-node` に EKS Pod Identity の関連付けがあること、それでもホストネットワークを使わない Pod が CNI からポッド IP を受け取れること、そして deny-all の ingress `NetworkPolicy` を適用する前は通っていた Pod 間の通信が、適用後には止まることである。これらはいずれも、e2e のこの時点ではまだ Karpenter を入れておらず、クラスタにシステムノードしか無い状態での確認であり、Karpenter が起動するノード(チームのワークロードが実際に乗るノード)の上で同じ振る舞いになるかは、この e2e では確かめていない。Argo CD の `role_mappings` は、run 1 から 7 までは IAM Identity Center のインスタンスが無い環境で実行したため確かめていなかったが、run 8(7 節)で、そのインスタンスがある `us-east-1` の専用 EC2 テストランナー上から確認している。

## 9. レビューと実機での 8 回の e2e

`infra/foundation` の本体は 22 ファイル、+2,408/-5 行で、このうち `karpenter.tf` が 561 行で全体の約 23% を占める。テナンシーの 4 点の追記はこれとは別に 5 ファイル、+181/-25 行で、最大は `eks.tf` の +133 行である。

実装、テストの作成、レビューを別々のセッションに分けて進めた。テストは実装のコードを読まずに Issue #20 の受け入れ基準だけから書いている。レビューで 17 件の指摘を受けた。12 件を直し、2 件は README に書いて説明するだけにし、2 件は保留のまま残し、1 件は制約そのものを外す形で応じた。e2e は 1 回につき課金対象のリソースを約 40 分動かす。

| 重大度 | 指摘 | 対応 |
| --- | --- | --- |
| 高 | セル名が長いと、EventBridge のルール名(`<name>-karpenter-<event>`)が 64 文字を超える | 直した。イベント名を短縮し、名前が最大 32 文字でも全ルールが 64 文字に収まるようにした |
| 高 | state のキーにリージョンが入っておらず、別リージョンで同じセル名を使うと最初のセルの state を乗っ取る | 直した。キーを `distai-platform/<region>/<name>/foundation` に変更した |
| 中 | CIDR の検証が `0.0.0.0/0` の文字列一致だけで、同じ広さを作る別の書き方(`/1` を 2 本など)を防げない | 直した。`/8` 以上かつネットワークアドレスであることを検証するようにした |
| 中 | 削除ガードが、Karpenter のノードロールを持つインスタンスプロファイルを無条件に全部消す(他の用途のプロファイルも消える恐れがある) | 直した。Karpenter 自身が付ける `owned` タグを確認してから消すようにした |
| 中 | 削除ガードに呼び出し元のアカウント確認が無い(別アカウントの認証情報だと何も消さずに PASS してしまう) | 直した。先頭で `sts get-caller-identity` を取り、セルのアカウントと一致しなければ失敗させるようにした |
| 中 | Karpenter コントローラの trust policy が `aws:SourceAccount` だけで、同じアカウントの別の Pod がロールを乗っ取れる | 直した。Pod Identity のセッションタグ 3 つを条件に追加した |
| 中 | システムノードグループが x86_64 専用であることが書かれていない | 書いて説明した。変数の説明と README に明記した |
| 中 | EC2 Spot のサービスリンクロールが要ることが書かれていない | 書いて説明した。`tests/README.md` の前提条件に追加した |
| 低 | IAM policy の description に Karpenter のバージョン番号(v1.15)が書かれていて、バージョンを上げるたびに文字列が古くなる | 直した。description からバージョン番号を外した |
| 低 | `tests/README.md` がセル名を「リージョン内で一意」と書いていたが、実際は IAM 名がアカウント全体で一意である必要がある | 直した。「アカウント全体で一意」に書き換えた |
| 低 | 削除ガードの ENI フィルタにクラスタのタグが無く、VPC 内の他の ENI も対象に見える | 残した。フィルタは VPC ID で絞っているため実害は無いと判断している。別クラスタの ENI が同じ VPC に混在する運用は想定していない |
| 低 | README がキューに送れるのは EventBridge のルールだけだと書いていたが、文面が実際のポリシーより狭く読めた | 直した。文面をポリシーの条件(このセルのルールの ARN からだけ)に合わせた |
| 低 | e2e の `terraform init` に `use_lockfile` が無く、同時に 2 つの run が同じ state を壊せる | 直した。`-backend-config="use_lockfile=true"` を追加した |
| 低 | 削除ガードの削除呼び出しが冪等でなく、1 回しか「空」を確認していない(消えかけのインスタンスを見落とす) | 直した。消えている資源は許容し(`tolerate_not_found`)、インスタンスが「無い」ことは 15 秒空けて 2 回確認するようにした |
| 低 | README と `tests/README.md` で環境変数名が揃っていない | 直した。`NAME` / `STATE_BUCKET` / `STATE_REGION` / `ADMIN_PRINCIPAL_ARN` / `MY_CIDR` に統一した |
| 低 | `kubernetes_version` の add-on バージョン存在チェックが `validation` ではなく `lifecycle.precondition` になっていて、他の検証と場所が揃っていない | 残した。このチェックは add-on バージョンの表(他のリソースのローカル値)を見るため、変数だけで書ける `validation` ブロックには書けない |
| 低 | システムノードグループの台数の上限が 10 に固定されていて、根拠のない制約に見える | 制約を外した。上限を無くし、1 台以上の任意の台数を許すようにした |

マルチテナンシーの 4 点(IMDSv2 の強制、CNI 権限の分離、`NetworkPolicy` の施行の有効化、Argo CD の `role_mappings`)を足した後、もう一度レビューを受けている。この回は、テストの確認範囲そのものが指摘の対象になった。IMDSv2 のトークンが取れないことを確認する Pod がホストネットワークを使わないものだけだったため、トークンが空なのが hop limit の効果なのか確認そのものが壊れているのか区別できなかった。これに対し、同じシステムノード上にホストネットワークを使う Pod も立て、そちらは必ずトークンを取れることを確認するよう変えた。`kube-system/aws-node` の Pod Identity の確認も、関連付けが存在することだけでなく、それが唯一の関連付けであり、かつ付与された先のロールが `AmazonEKS_CNI_Policy` だけを持つことまで確かめるようにした。`argocd` や CIDR の入力検証は e2e では一度も踏まないパスなので、モックしたプロバイダに対する `terraform test` を足して別に確認するようにした。README の記述も直し、ノードロールの認証情報には CNI の権限が無くなった一方で、ノード上で root 権限を持つプロセスは `aws-node` のトークンを読めるので、その経路までは塞いでいないと明記した。あわせて、Karpenter が起動するノードのメタデータ設定(IMDSv2 の強制など)は `EC2NodeClass` 側で明示する必要があることも書き添えた。

この `terraform test` は後から 8 件まで増えた。`argocd` の `role_mappings` が 3 ロールとも受け付けられること、未知のロール名を拒否すること、`group_ids` が空だと拒否すること、同じロールを 2 回使うと拒否すること、`role_mappings` が 0 件だと拒否すること、公開エンドポイントの CIDR が広すぎると拒否すること、CIDR のリストが空だと拒否すること、そして `system_node_group.instance_types` に Graviton(Arm64)系のインスタンスタイプを渡すと拒否することの 8 件で、いずれも実際の AWS 呼び出しを一切せずに通る。最後の 1 件は 1 節で触れた「システムノードグループは x86_64 専用」という制約を、README の記述だけでなくコードの `validation` ブロックでも強制していることの確認である。

`tests/1.e2e.sh` 自身にも 2 つの安全策が後から入った。1 つは、apply の前に state を確認し、キー `distai-platform/<region>/<name>/foundation` に既にリソースが残っていたら、空の state だと確かめられるまで先へ進まない。これにより、前回の run が片付けに失敗した state の上に次の run が重ねて apply することがなくなる。もう 1 つは、destroy 後の残存チェック(`report_leftovers`)で、AWS 呼び出しそのものが失敗した場合と、呼び出しが成功して「0 件」が返った場合を区別するようにしたことである。前者はチェックを失敗させ、後者だけを PASS とする。この区別が無いと、一時的な認証エラーや権限不足で AWS 呼び出し自体が失敗したときに、結果が「0 件」と誤認され、本当は残っているリソースを見落としたまま PASS してしまう。

実機の e2e は、東京リージョン(`ap-northeast-1`)で `infra/foundation/tests/1.e2e.sh` を 7 回走らせ、加えて `us-east-1` の専用 EC2 テストランナー上で 1 回(下の run 8)走らせている。

| run | コード | 結果 |
| --- | --- | --- |
| 1 | レビュー前 | FAIL。`terraform output -json argocd_capability_arn` が `Error: Output "argocd_capability_arn" not found` を返した |
| 2 | レビュー前(テスト修正後) | PASS。apply 636 秒、destroy 883 秒 |
| 3 | レビュー後(修正 commit `00afa70` を反映) | PASS。apply 658 秒、destroy 860 秒 |
| 4 | マルチテナンシーの 4 点を足した後(commit `66c8cf8` に至る前の時点) | FAIL。`NetworkPolicy` の確認用に立てた 2 つの Pod(`netpol-server`、`netpol-client`)がシステムノードの `CriticalAddonsOnly=true:NoSchedule` taint に対する toleration を持たず、`Pending` のまま `Ready` を待ってタイムアウトした(`2 node(s) had untolerated taint(s)`)。apply は 669 秒で成功し、同じ run の中で IMDS・CNI 権限・Pod Identity の確認は先に PASS していた。失敗を検知した後のクリーンアップの `terraform destroy` も完了している |
| 5 | テスト側の修正(IMDS 確認用の Pod と同じ `nodeSelector`・toleration を `netpol-server`/`netpol-client` にも付けた)を反映 | PASS。apply 673 秒、destroy 900 秒。全体で約 31 分 |
| 6 | マルチテナンシーのレビュー後(`fc1afc0`)を反映 | スクリプトは PASS(apply 618 秒、destroy 856 秒)。だが削除ガードのログに起動テンプレートを消す行が無く、残存チェックもインスタンス・ENI・NAT ゲートウェイ・ボリュームの 4 種類しか見ていなかった。Verifier(実機で確かめるセッション、10 章)がスクリプトの外でタグを洗い直し、Karpenter が作った起動テンプレートが 1 つ残っていることを見つけ、手動で削除した |
| 7 | 削除ガードの修正(`7884eaf`)を反映 | PASS。apply 676 秒、destroy 859 秒。全体で約 32 分。削除ガードのログに「起動テンプレートを削除」の行が出力され、残存チェックにも起動テンプレートの種類が加わった |
| 8 | head `db7812c`(テストした実装は `a78ae5d`、head は README の文言のみの差)。セル自体を `us-east-1` に構築(Argo CD capability の IAM Identity Center へのサインインがこのリージョンのインスタンスを必要とするため。Terraform の state バケットは `ap-northeast-1` のまま)、専用 EC2 テストランナー上で、一時的な Identity Center グループ 3 つを使って実行 | PASS。apply 1041 秒、destroy 925 秒。全体で約 37 分。Argo CD capability が `ACTIVE` になり、`ADMIN`・`EDITOR`・`VIEWER` 各ロールのグループ ID が capability の設定に反映されていることを確認。あわせて、4 節・5 節で見たマルチテナンシーの IMDSv2・CNI 権限分離・`NetworkPolicy` 施行の 3 点も同じ run の中で再確認 |

1 回目の失敗は、基盤のコードではなくテストスクリプトの思い込みが原因だった。README の契約(`argocd` を設定しなければ `argocd_capability_arn` は `null`)は正しく実装されていたが、Terraform はルート出力の値がちょうど `null` のとき、その出力を state から完全に省いてしまう。そのため名前で `terraform output` を呼ぶと、`null` ではなく「出力が見つからない」エラーになる。これは Terraform のバージョン(確かめたのは 1.17.2)の挙動であり、独立した最小構成の Terraform で再現を確認した。原因が確定してから、テストスクリプト側が、その特定の失敗を `null` と同値に扱うように直した。この挙動は、テストスクリプトだけの問題ではない。2 節で見た「後の層はこの層の出力だけを読む」という契約を字面どおりに実装し、名前を指定して `argocd_capability_arn` を読む側(後で作る層や、手元で `terraform output` を叩く運用者)も、`argocd` を設定していないセルに対しては同じ「出力が見つからない」エラーを踏む。`null` を返す出力として契約している以上、読む側は `try(..., null)` のような形で呼ぶ必要がある。

3 回目はレビュー後のコード(コントローラの trust policy にセッションタグを足したもの)で再実行した結果であり、マルチテナンシーの 4 点を足す前の時点では、これが PR の `## Test Results` に貼った証拠だった。セッションタグを足しても Karpenter の Pod Identity の認証情報は通常どおり発行され、EC2NodeClass が Ready になり、spot インスタンスの起動・中断・置き換えの一連の流れが問題なく動いた。

4 回目の失敗も、1 回目と同じ種類の原因で、基盤のコードではなくテストスクリプト側の見落としだった。`NetworkPolicy` の確認は、通信が通る状態と止まる状態を比べるために `netpol-server`/`netpol-client` という 2 つの Pod をシステムノードグループの上に立てる設計だが、この 2 つの Pod には `CriticalAddonsOnly` の toleration がなく、ノードにスケジュールされないまま `Ready` 待ちがタイムアウトした。IMDS の確認用に別に立てていた Pod には同じ toleration が既に付いていたので、それと揃えるだけで直った。

5 回目が、マルチテナンシーの 4 点を足した後の最初の成功であり、ホストネットワークを使わない Pod はシステムノード上で IMDSv2 のトークンを取得できず、システムノードの EC2 インスタンスは `HttpTokens = required` かつ `HttpPutResponseHopLimit = 1` だった。Karpenter のノードロールもシステムノードロールも `AmazonEKS_CNI_Policy` を持たず、`kube-system/aws-node` には EKS Pod Identity の関連付けがあり、それでもホストネットワークを使わない Pod はポッド IP を受け取れた。`NetworkPolicy` を適用する前はクライアント Pod からサーバー Pod への通信が通り、deny-all の ingress `NetworkPolicy` を適用した後は同じ通信が止まった。そのうえで Karpenter 1.15.2 のインストール(`controller.env` に `AWS_REGION` を明示)、spot インスタンスの起動、スポット中断通知への反応、置き換えインスタンス(最初が `m5.large`、中断後の置き換えが `m7i.large`)への引き継ぎも、いずれも問題なく動いた。実行後の独立した確認でも、EKS クラスタ、VPC、インスタンス、ENI、NAT ゲートウェイ、Elastic IP、ボリューム、IAM ロール(`aws-node` 用の Pod Identity ロールも含む)、インスタンスプロファイル、Pod Identity の関連付け、起動テンプレート(システムノード専用のものも含む)、SQS キュー、EventBridge のルール、ロググループのいずれも、このセルに属するものは 1 つも残っておらず、state オブジェクトが抱えるリソースは 0 件だった。

6 回目(`fc1afc0`)は、スクリプトの判定だけを見れば成功であり、一時はこれが PR の `## Test Results` に貼られていた。ところが Verifier がスクリプトの外で残存リソースのタグを洗い直したところ、削除ガードが「Karpenter の起動テンプレートを消した」というログを一行も出していないことに気づいた。削除ガードは起動テンプレートをタグキー `karpenter.k8s.aws/cluster` で絞り込んでいたが、このタグキーは Karpenter v1.15.2 が自分では一度も付けないものだった。Karpenter 本体のソース(`pkg/apis/v1/labels.go`)を見ると、クラスタ名を運ぶタグキーは `EKSClusterNameTagKey` という定数で、実際の値は `eks:eks-cluster-name` であると定義されている。つまり削除ガードの絞り込みは存在しないタグキーで検索していたために常に 0 件を返し、Karpenter が作った起動テンプレートを一度も削除していなかった。`1.e2e.sh` の残存チェックにも起動テンプレートという種類が無く、この欠陥は 6 回目より前の回でも同じように起動テンプレートを残し続けていた可能性が高いが、どの回でもスクリプト自身は検知できずに PASS し続けていた。6 回目で初めて見つかったのは、たまたまこの回で Verifier がスクリプトの外側まで広くタグを洗い直したからである。

直した `7884eaf` では、削除ガードが起動テンプレートを `eks:eks-cluster-name = <セル名>` と、タグキー `karpenter.k8s.aws/ec2nodeclass` の両方で絞り込むようにし、`1.e2e.sh` の残存チェック(`report_leftovers`)にも起動テンプレートという種類を加えた。7 回目はこの修正の確認であり、削除ガードのログに実際に「起動テンプレートを削除」という行が出力され、残存チェックの新しい項目も PASS した。独立した事後確認でも、タグ `eks:eks-cluster-name` の値がこのセル名である起動テンプレートは 0 件であり、タグキー `karpenter.k8s.aws/ec2nodeclass` を持つ起動テンプレートを広く検索しても、別の既存クラスタのものが 1 件見つかるだけで、このセルに属するものは残っていなかった。

この一件が示すのは、e2e が PASS したという結果は、スタックが作るすべての種類のリソースについて残存チェックが書かれていない限り、後片付けが完了した証拠にならないという点である。残存チェックに載っていない種類のリソースは、作られても消されても、PASS の文字列には何も現れない。もう一つ、これは新規構築の e2e では塞ぎきれない種類の欠陥でもある。state のアドレス移動、`ForceNew` を伴う変更、権限を付ける順番の変更といった、アップグレード経路でしか踏まれない分岐は、ゼロから作って壊す e2e では一度も通らない。

8 回目は、7 節で述べた、それまで実機では確認できていなかった Argo CD capability の `role_mappings` の分岐を、初めて実機で確かめた run である。IAM Identity Center のインスタンスが既にある `us-east-1` で、専用の EC2 テストランナー上から実行し、`ADMIN`・`EDITOR`・`VIEWER` の 3 ロールに割り当てる一時的なグループを `aws identitystore create-group` で作り、確認後に `delete-group` で消している。テストした実装のコミットは `a78ae5d` で、head の `db7812c` はそこから README の文言を直しただけである。この run では、capability が `ACTIVE` になることと、`role_mappings` に渡した 3 つのグループ ID が capability 自身の設定に反映されていることの両方を確認した。1 回の apply が 1041 秒、destroy が 925 秒で、全体では約 37 分だった。run 後の独立した確認でも、EKS クラスタ、capability、Pod Identity の関連付けを含め、このセルに属するリソースは何も残っていなかった。

<!-- FIG name="08-e2e" kind="flow" shows="3回のe2e実行の流れと、1回目の失敗がテストスクリプトのnull扱いバグだったこと" labels="run1 FAIL, run2 PASS, run3 PASS, argocd_capability_arn, null output, commit 00afa70" -->
![3回のe2e実行の流れを示す図](https://raw.githubusercontent.com/littlemex/figures/refs/heads/zenn/books/distai-platform-design/zenn/books/distai-platform-design/08-e2e.png)

この層で選んだことのうち、GPU と Neuron のどちらのセルにもそのまま当てはまり、Trainium を前提にしないものは、`awsome-distributed-ai` 側への提案の候補でもある。公開エンドポイントを CIDR で絞れるように `architectures/amazon-eks` のテンプレートにパラメータを 1 つ足すこと、クラスタ作成者を暗黙の管理者にしないこと(同じテンプレートの数行の修正で済む)、そして Karpenter を使う Terraform モジュール([`architectures/sagemaker-hyperpod-eks`](https://github.com/awslabs/awsome-distributed-ai/tree/25d5723cb62ab1f83b0f4307c28c8f3496bfaa53/architectures/sagemaker-hyperpod-eks))に 6 節で見た削除ガードと同種の仕掛けを足すことの 3 つである。いずれもまだ提案としては出していない候補であり、Trainium に特有の知見はここには含めない。アップストリームへの貢献の方針そのものは 11 章で扱う。

この章を書いている時点(2026 年 11 月 18 日)で、PR #21 は `status: in review` でマージ待ちである。main のブランチルールセット(PR 必須、force push 禁止)もまだ設定されていない。

## まとめ

`infra/foundation` は、Terraform に「1 セル分の入れ物だけ」を作らせるという Issue #17 と #19 の決定を、VPC、EKS、システムノード、Karpenter の前提、削除ガードの 5 つの要素で実装したものである。レビューの指摘 17 件のうち 12 件を直し、残り 5 件(文書化 2、保留 2、制約緩和 1)は理由とともに本文に残した。その後、1 つのセルを複数チームで共有する運用([Issue #22](https://github.com/littlemex/distai-platform/issues/22) のテナンシー層)に向けて、システムノードの IMDSv2・hop limit 1、ノードロールからの CNI 権限の分離、`NetworkPolicy` 施行の有効化、Argo CD の `role_mappings` の 4 点を足した。実機の e2e を重ねるなかで、1 回目の失敗は Terraform の `null` 出力の扱いに対するテストスクリプト側の思い込み、4 回目の失敗は `NetworkPolicy` 確認用の Pod に taint への toleration が無かったという、どちらもテストスクリプト側の誤りだったことを原因調査で確定させている。6 回目は PASS 判定が出たにもかかわらず、削除ガードの残存チェックが見ていなかった起動テンプレートが 1 つ残っており、PASS という結果そのものが後片付けの証明にならないことを示した。7 回目はこの欠陥を直したうえでの再実行であり、8 回目はセル自体を IAM Identity Center のインスタンスがある `us-east-1` に構築し、専用 EC2 テストランナー上から、一時的なグループを使って、capability が `ACTIVE` になることと `role_mappings` が設定に反映されることを初めて実機で確認した run である(各ロールでのサインインや権限の効き方そのものは未確認のまま残る)。`terraform test` も、`argocd` の `role_mappings` の受理・拒否 5 件、CIDR の拒否 2 件、Graviton(Arm64)系インスタンスタイプの拒否 1 件の計 8 件に増え、e2e 自身にも、既存リソースの上に apply しないための空 state チェックと、AWS 呼び出しの失敗を「0 件」と誤認しない残存チェックが入った。テナント Pod をシステムノードから締め出す admission policy、実際の `NetworkPolicy` オブジェクト、チームごとの `AppProject` は、この層の範囲外として #22 に残されている。削除ガード自身にも、`terraform apply` が途中で失敗した後の `terraform destroy` ではガードが一度も作られておらず後片付けが効かないという欠陥が [Issue #26](https://github.com/littlemex/distai-platform/issues/26) として残っている。
