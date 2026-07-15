---
title: "Voice Image Edit on Trainium: 4 models, 64 NeuronCores, one deploy"
emoji: "🎤"
type: "tech"
topics: ["AWS", "Neuron", "Trainium", "vLLM", "CDK"]
published: false
---

## 1. Introduction

**Trainium2** is AWS's custom silicon for ML training and inference. Unlike GPUs, a Trainium chip exposes dedicated compute units called **NeuronCores**, programmed through the [AWS Neuron SDK](https://awsdocs-neuron.readthedocs-hosted.com/) rather than CUDA. The instance used in this article, `trn2.48xlarge`, carries 16 Trainium2 chips connected in a 4x4 torus; with the default LNC=2 (Logical NeuronCore) configuration, each chip presents 4 logical cores — 64 in total. Models are compiled ahead of time into Neuron Executable File Format (NEFF) artifacts, which is why compilation time and caching play a much bigger role than in a typical GPU workflow.

Large Trainium (and GPU) instances are often impossible to obtain on-demand. The practical alternative is a **Capacity Block (CB)**: a time-boxed reservation with a fixed start and end time. When the block expires, the instance is *forcibly terminated* and its local NVMe storage is wiped. This changes how you design deployments — anything you can't afford to lose (compiled models, weights, server code) must live somewhere that survives termination, and anything slow (compilation) must be cacheable so a fresh instance can come back quickly. A `trn2.48xlarge` Capacity Block costs approximately $36/hour, with a typical minimum purchase of 24 hours.

Why build this demo? Because **running multiple different models simultaneously on one Trainium instance is non-trivial**. On GPUs, CUDA MPS and time-sharing let processes dynamically share a pool of cores. NeuronCores have no such mechanism: allocation is *static per process*, set via environment variables before the process starts. Packing four models of different frameworks (NxD, vLLM, custom pipelines) onto one instance requires explicit core partitioning, alignment-aware layout, and per-model tensor-parallelism decisions — a pattern that isn't well documented anywhere. This article demonstrates a working 4-model layout in detail.

The demo itself, **Voice Image Edit**, is a multimodal pipeline: a voice instruction is transcribed (ASR), interpreted against an image (VLM), the image is edited (diffusion), and a confirmation is spoken back (TTS) — all on Trainium, with no GPU or external API dependency for inference. Each slot can alternatively run on Bedrock, so the demo doubles as a comparison point: it shows that a fully self-hosted, Bedrock-free multimodal inference stack is feasible on a single Trainium instance. This is a companion to the [Neuron Agentic Development setup article](https://zenn.dev/tosshi/articles/f3f678f4b6531c), which covered deploying a single Trainium workstation with Claude Code integration; this article assumes you have completed that setup. Reading time: ~15 minutes. The source lives at [`littlemex/aws-neuron-samples`](https://github.com/littlemex/aws-neuron-samples), primarily under [`samples/voice-image-edit/`](https://github.com/littlemex/aws-neuron-samples/tree/main/samples/voice-image-edit), with model pipelines under [`samples/models/`](https://github.com/littlemex/aws-neuron-samples/tree/main/samples/models) and the monitoring stack under [`samples/neuron-anatomy/`](https://github.com/littlemex/aws-neuron-samples/tree/main/samples/neuron-anatomy).

### What this article covers

1. **Pipeline architecture** — how `deploy-all.sh` orchestrates compilation, serving, and CDK deployment in 5 phases
2. **Neuron Anatomy** — a custom real-time monitoring module that streams NeuronCore utilization to the browser via SSE (built on top of the official `neuron-monitor` CLI)
3. **NeuronCore allocation** — how 4 models are mapped to 64 logical cores with tensor parallelism
4. **EFS persistence strategy** — surviving Capacity Block termination without losing compiled models
5. **Capacity Block lifecycle** — cold start vs warm recover and cost implications

---

## 2. Application overview

Voice Image Edit has four inference slots, each switchable between a Bedrock-backed implementation and a self-hosted Trainium implementation:

| Slot | Bedrock | Trainium | Port |
|------|---------|----------|------|
| ASR | Amazon Transcribe (streaming) | Whisper Large v3 (NxD) | 8765 |
| VLM | Claude Opus 4.5 | Qwen3-VL 8B (vLLM) | 8090 |
| Edit | Amazon Nova Canvas | Qwen-Image-Edit 2511 | 8081 |
| TTS | Amazon Polly | XTTSv2 (NxD) | 8770 |

The application layer consists of three services deployed as systemd units behind an ALB + CloudFront + Cognito stack:

- **API** (port 8801): FastAPI backend handling edit requests
- **Stream** (port 8800): SSE streaming for real-time progress
- **Frontend** (port 3000): Next.js 14 standalone

Source: [`samples/voice-image-edit/app/`](https://github.com/littlemex/aws-neuron-samples/tree/main/samples/voice-image-edit/app)

---

## 3. Pipeline architecture: deploy-all.sh

![](/images/9dc18j5mqdcz19/01-pipeline-flow.png)
*The pipeline flow diagram above shows the 5-phase structure of deploy-all.sh, with S3 tarballs feeding the compilation phase and model servers fanning into the application layer.*

The entire deployment is driven by a single script: [`samples/voice-image-edit/scripts/deploy-all.sh`](https://github.com/littlemex/aws-neuron-samples/blob/main/samples/voice-image-edit/scripts/deploy-all.sh). It runs 5 phases sequentially, with a custom YAML pipeline runner ([`tools/pipeline-runner/`](https://github.com/littlemex/aws-neuron-samples/tree/main/tools/pipeline-runner)) executing each step via SSM Run Command. The runner treats an existing EFS artifact as a skip condition, making the entire flow idempotent.

### Phase 1: Infrastructure prep

```bash
cd aws-neuron-samples/samples/voice-image-edit/scripts
bash deploy-all.sh --base-stack-name storeai-validation-use2 --region us-east-2 --recover
```

The script first runs `precheck` to fetch CloudFormation outputs (instance ID, EFS ID, public IP), then `setup-efs-paths` to ensure EFS symlinks are in place. `migrate-to-efs` runs only on the first deploy; `--recover` skips it.

### Phase 2: Model compilation (parallel, EFS cache skip)

Four models are compiled for Neuron in parallel. Each compilation is a YAML pipeline under `samples/models/<name>/pipelines/`:

- **whisper-precompile**: NxD Inference path, TP=8 ([`compile_whisper_nxd.py`](https://github.com/littlemex/aws-neuron-samples/blob/main/samples/models/whisper/compile_whisper_nxd.py))
- **qwen3-vl-prepare**: vLLM warmup with Neuron backend ([`qwen3-vl/`](https://github.com/littlemex/aws-neuron-samples/tree/main/samples/models/qwen3-vl))
- **qwen-image-edit-prepare**: Classifier-free guidance (CFG) with 5 parallel denoising components — text encoder, image encoder, and 3 U-Net variants run concurrently ([`qwen-image-edit/`](https://github.com/littlemex/aws-neuron-samples/tree/main/samples/models/qwen-image-edit))
- **xttsv2-precompile**: GPT decoder compile in DLC, BF16 ([`xttsv2/`](https://github.com/littlemex/aws-neuron-samples/tree/main/samples/models/xttsv2))

On a warm recover (EFS cache hit), these steps complete in seconds with a "skip" message.

### Phase 3: Model servers (parallel, systemd units)

Each compiled model gets a systemd service with a health-check endpoint. The [common YAML pipeline pattern](https://github.com/littlemex/aws-neuron-samples/tree/main/samples/models/whisper/pipelines/whisper-nxd-server) is:

```
precheck → stop-service → deploy-tarball → install-systemd-unit → enable-start → health-check
```

Source tarballs are uploaded to an S3 staging bucket with presigned URLs, then extracted on the instance via SSM Run Command. The pipeline runner tracks state in a per-instance JSON file under `.runner-state/` so interrupted deploys can resume.

### Phase 4: Application layer (CDK)

[`app/infra/deploy.sh`](https://github.com/littlemex/aws-neuron-samples/blob/main/samples/voice-image-edit/app/infra/deploy.sh) deploys three CDK stacks (VoiceImageEditApiStack, StreamStack, FrontendStack) that wire the model server URLs into environment variables and attach ALB target groups.

### Phase 5: Neuron Anatomy (sibling stack)

[`neuron-anatomy/scripts/deploy.sh`](https://github.com/littlemex/aws-neuron-samples/tree/main/samples/neuron-anatomy) re-deploys the anatomy backend so its ALB target group points to the current EC2 private IP (which changes on every recover).

---

## 4. Neuron Anatomy: real-time monitoring

![](/images/9dc18j5mqdcz19/02-neuron-anatomy.png)
*The data flow diagram shows the 4-layer architecture: neuron-monitor subprocess outputs NDJSON, the FastAPI backend normalizes and fans out via SSE, and the React frontend renders a per-core heatmap.*

Neuron Anatomy provides a real-time view of NeuronCore utilization, memory pressure, and chip topology directly in the browser.

### Data flow

1. **neuron-monitor** (`/opt/aws/neuron/bin/neuron-monitor`) runs as a subprocess, outputting NDJSON at 1-second intervals
2. **MonitorService** ([`monitor.py`](https://github.com/littlemex/aws-neuron-samples/blob/main/samples/neuron-anatomy/backend/neuron_anatomy/monitor.py)) parses the stream, normalizes it into a `Snapshot` schema, and fans out to per-subscriber `asyncio.Queue` instances
3. **router.py** exposes `GET /neuron/stream` (SSE), `GET /neuron/snapshot` (polling), `GET /neuron/topology` (static), and `GET /neuron/health`
4. **ALB + CloudFront** pass the SSE stream through without buffering. The CloudFront behavior for `/neuron/*` has caching disabled and the response includes `Cache-Control: no-cache` to prevent intermediate proxies from buffering events
5. **React frontend** uses `useNeuronStream` (EventSource API, auto-reconnect) and `useNeuronTopology` (one-shot fetch) hooks to render a per-core heatmap and torus topology visualization

### Topology detection

[`topology.py`](https://github.com/littlemex/aws-neuron-samples/blob/main/samples/neuron-anatomy/backend/neuron_anatomy/topology.py) calls `neuron-ls -j` once at startup to discover chips, edges, and engine specs. No chip count or adjacency is hardcoded — trn2.48xlarge (16 chips, 4x4 torus) and Trn2 UltraServer (64 chips) are handled by the same code path. For local development without a Neuron instance, set `NEURON_ANATOMY_FAKE_TOPOLOGY=1` to enable a synthetic topology.

---

## 5. NeuronCore allocation: 4 models on 64 cores

![](/images/9dc18j5mqdcz19/03-neuroncore-allocation.png)
*As the allocation map above shows, the four models tile the 4x4 torus left-to-right: XTTSv2 (lightest green) occupies Device 0, Whisper spans two devices, Qwen3-VL takes four, and Qwen-Image-Edit fills the remaining eight devices. Cores 4-7 (grey) are spare capacity.*

The `trn2.48xlarge` has 16 NeuronDevices, each with 8 physical NeuronCores. With the default LNC=2 configuration, these present as 4 logical NeuronCores per device — 64 logical cores total, connected by a 4x4 torus interconnect.

### Allocation map

| Model | TP | Cores | Port | Framework | Notes |
|-------|-----|-------|------|-----------|-------|
| XTTSv2 | 4 | 0-3 | 8770 | NxD (container) | Device 0 only (see below) |
| Whisper Large v3 | 8 | 8-15 | 8765 | NxD Inference | |
| Qwen3-VL 8B | 16 | 16-31 | 8090 | vLLM (neuron) | |
| Qwen-Image-Edit | 32 | 32-63 | 8081 | Custom | CFG 5-component parallel |

Cores 4-7 are unallocated. This gap exists because Whisper requires TP=8 starting at an 8-aligned offset (core 8), and XTTSv2 occupies only 4 cores on Device 0.

### Why this layout?

The allocation is defined in the `xttsv2_profile_for_instance()` function and `NEURON_CORES` variables in `deploy-all.sh`, plus each model server's pipeline YAML. For example:

```yaml
# whisper-nxd-server.yml
vars:
  NEURON_CORES: "8-15"      # pipeline variable, maps to NEURON_RT_VISIBLE_CORES in systemd unit
```

The pipeline runner translates `NEURON_CORES` into `NEURON_RT_VISIBLE_CORES` (the actual Neuron runtime environment variable) when generating the systemd unit file.

**XTTSv2 Device 0 pinning**: The containerized neuron-rt 2.30 has a known issue where a PCI bus-device-function workaround assertion fires on high-index NeuronDevices. Until this is fixed upstream, XTTSv2 must stay on Device 0 (cores 0-3).

**Qwen-Image-Edit 32 cores**: This model uses classifier-free guidance (CFG) where 5 denoising pipeline components (text encoder, image encoder, 3 U-Net stages) run in parallel. Each component needs its own set of cores, making TP=32 the minimum for acceptable latency.

---

## 6. EFS persistence: surviving Capacity Block termination

![](/images/9dc18j5mqdcz19/04a-storage-tiers.png)
*The storage tiers diagram shows how symlinks (/models, /opt/voice-image-edit) point into EFS, while NVMe serves as a fast ephemeral cache with periodic rsync backup.*

### Storage tiers

| Tier | Mount | Lifetime | Contents |
|------|-------|----------|----------|
| NVMe Instance Store | /mnt/local | Ephemeral (wiped on terminate) | Active compile cache |
| EFS | /mnt/efs | Persistent (survives terminate) | Models, NEFF, HF weights, server code |
| Root EBS | / | Per-instance | OS, packages, venvs |

The key insight: **model data never lives on the instance itself**. Symlinks make this transparent:

```
/models → /mnt/efs/neuron-workspace/models/
/opt/voice-image-edit → /mnt/efs/neuron-workspace/voice-image-edit/
```

A systemd timer runs `rsync ~/.cache → EFS` every 10 minutes as a backup of the NVMe compile cache. On recover, the reverse rsync restores it.

Source: [`setup/single-node/scripts/setup-persistence.sh`](https://github.com/littlemex/aws-neuron-samples/blob/main/setup/single-node/scripts/setup-persistence.sh)

---

## 7. Capacity Block lifecycle: cold start vs warm recover

![](/images/9dc18j5mqdcz19/04b-cb-lifecycle.png)
*The lifecycle diagram shows the linear flow from CB expiration through EFS persistence to warm recovery. The bottom annotation highlights the 1-3h vs 15min difference.*

Capacity Blocks are time-limited reservations. When they expire, the instance is terminated and NVMe storage is wiped. The EFS filesystem persists independently.

### Recovery flow

```bash
# From setup/single-node/scripts/:

# 1. Purchase new CB
./manage-capacity-block.sh search -t trn2.48xlarge -r us-east-2
./manage-capacity-block.sh purchase --offering-id <id> --start-time <time>

# 2. Recover base infra + all services (single command)
./deploy.sh -r us-east-2 -t trn2.48xlarge --use-capacity-block --slot voice-edit \
  --stack-name storeai-validation-use2 --recover
```

The `--recover` flag:
1. Detects the terminated instance via CloudFormation outputs
2. Triggers CDK deploy to create a new EC2 with the new CB reservation
3. Mounts EFS, restores NVMe cache
4. Runs `deploy-all.sh --recover` which skips all `*-precompile` steps (EFS cache hit)
5. Restarts model servers and updates ALB targets

### Time and cost comparison

| Scenario | Duration | CB cost on compilation |
|----------|----------|------------------------|
| Cold start (first deploy) | 1-3 hours | $36-108 |
| Warm recover (EFS cache) | ~15 minutes | ~$9 |

On every recover, EFS eliminates 1-3 hours of recompilation time. Since the minimum CB purchase is typically 24 hours ($858), a recompile that overruns your current reservation would force purchasing an additional block — making EFS persistence critical for cost efficiency.

---

## 8. Running it yourself

Prerequisites: the base stack from the [setup article](https://zenn.dev/tosshi/articles/f3f678f4b6531c) deployed with `--full` (EFS + ALB + Cognito + CloudFront).

```bash
cd aws-neuron-samples/samples/voice-image-edit/scripts

AWS_PROFILE=claude-code bash deploy-all.sh \
  --base-stack-name <your-stack-name> \
  --region <your-region> \
  --recover  # or omit for first deploy
```

The CloudFront URL from your frontend stack output will serve the application with Cognito login.

---

## 9. Conclusion

Voice Image Edit demonstrates that a single Trainium instance can serve a complete multi-model inference pipeline — ASR, VLM, image editing, and TTS — with all 64 logical NeuronCores allocated across four models of different frameworks (NxD, vLLM, custom). The EFS persistence layer and `--recover` automation turn Capacity Block's time-limited nature from a limitation into a cost optimization: you pay only for the compute hours you actually use for inference, not for recompilation.

The architecture patterns shown here — YAML pipeline runner, EFS-backed model cache, Neuron Anatomy real-time monitoring, and multi-model core allocation — are reusable building blocks for any Trainium inference workload.