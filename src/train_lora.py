"""QLoRA fine-tuning for stage-1 skeleton restoration. Two arms:

  --arm ar    Qwen2.5-Coder-7B, standard CE on target tokens (prompt masked out)
  --arm diff  DiffuCoder-7B, masked-diffusion SFT loss (LLaDA recipe):
              t ~ U(eps,1) per sample, mask target tokens w.p. t, CE on masked
              positions weighted 1/t, normalized by target length.

Hardware: training runs on A100 80GB. Default path is bf16 full-precision LoRA
(--quant bf16) with gradient checkpointing off and length-bucketed batching, to
maximize throughput. The 4-bit NF4 path (--quant nf4, +--grad-checkpoint) is kept
as a fallback for small-VRAM cards. Identical adapter budget across arms preserves
comparison fairness regardless of quant mode.

Usage (pilot, A100 80GB):
  train_lora.py --arm diff --data pilot2k_balanced.jsonl --out checkpoints/diff_pilot \
      --seed 0 --max-steps 200 --seq-len 2048 \
      --quant bf16 --micro-batch 8 --grad-accum 2 --bucket
"""

import argparse
import json
import math
import os
import random
import time

import torch
from torch.utils.data import Dataset, DataLoader

DEFAULT_MODELS = {"ar": "Qwen/Qwen2.5-Coder-7B", "diff": "apple/DiffuCoder-7B-Base"}

PROMPT_TMPL = "### Pseudocode:\n{pseudo}\n### Skeleton:\n"


class PairDataset(Dataset):
    def __init__(self, path, tokenizer, seq_len, arm):
        self.rows = []
        self.tok = tokenizer
        self.seq_len = seq_len
        self.arm = arm
        skipped = 0
        for line in open(path):
            r = json.loads(line)
            prompt = PROMPT_TMPL.format(pseudo=r["input"])
            target = r["target"] + (tokenizer.eos_token or "")
            p_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
            t_ids = tokenizer(target, add_special_tokens=False)["input_ids"]
            if len(p_ids) + len(t_ids) > seq_len:
                skipped += 1
                continue
            self.rows.append((p_ids, t_ids))
        print(f"dataset: {len(self.rows)} usable, {skipped} skipped (>{seq_len} tok)")

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        return self.rows[i]


def collate(batch, pad_id):
    maxlen = max(len(p) + len(t) for p, t in batch)
    input_ids, prompt_lens, total_lens = [], [], []
    for p, t in batch:
        ids = p + t
        pad = [pad_id] * (maxlen - len(ids))
        input_ids.append(ids + pad)
        prompt_lens.append(len(p))
        total_lens.append(len(ids))
    return (torch.tensor(input_ids), torch.tensor(prompt_lens), torch.tensor(total_lens))


class BucketBatchSampler:
    """Length-sorted batches, batch order reshuffled each epoch (seeded)."""

    def __init__(self, lengths, batch_size, seed):
        self.order = sorted(range(len(lengths)), key=lambda i: lengths[i])
        self.batch_size = batch_size
        self.g = torch.Generator().manual_seed(seed)

    def __iter__(self):
        batches = [self.order[i:i + self.batch_size]
                   for i in range(0, len(self.order), self.batch_size)]
        for p in torch.randperm(len(batches), generator=self.g).tolist():
            yield batches[p]

    def __len__(self):
        return (len(self.order) + self.batch_size - 1) // self.batch_size


def ar_loss(model, input_ids, prompt_lens, total_lens, pad_id):
    labels = input_ids.clone()
    for i in range(len(labels)):
        labels[i, : prompt_lens[i]] = -100
        labels[i, total_lens[i]:] = -100
    attn = (torch.arange(input_ids.shape[1])[None, :].to(input_ids.device)
            < total_lens[:, None].to(input_ids.device)).long()
    out = model(input_ids=input_ids, attention_mask=attn, labels=labels)
    return out.loss


def diff_loss(model, input_ids, prompt_lens, total_lens, mask_id, eps=1e-3):
    """LLaDA-style masked diffusion SFT: mask only response tokens."""
    b, L = input_ids.shape
    device = input_ids.device
    t = torch.rand(b, device=device) * (1 - eps) + eps
    pos = torch.arange(L, device=device)[None, :]
    is_target = (pos >= prompt_lens[:, None].to(device)) & (pos < total_lens[:, None].to(device))
    mask_draw = torch.rand(b, L, device=device) < t[:, None]
    masked = is_target & mask_draw
    # ensure at least one masked token per sample
    for i in range(b):
        if not masked[i].any() and is_target[i].any():
            idx = is_target[i].nonzero()[0]
            masked[i, idx] = True
    noisy = input_ids.clone()
    noisy[masked] = mask_id
    key_valid = pos < total_lens[:, None].to(device)            # (b, L)
    attn = key_valid[:, None, None, :]                          # (b,1,1,L) bool pad mask, bidirectional, for DreamModel SDPA
    logits = model(input_ids=noisy, attention_mask=attn).logits
    ce = torch.nn.functional.cross_entropy(
        logits[masked], input_ids[masked], reduction="none")
    # weight 1/t per sample, normalize by target length
    sample_idx = masked.nonzero()[:, 0]
    w = 1.0 / t[sample_idx]
    tgt_len = (total_lens.to(device) - prompt_lens.to(device)).clamp(min=1)
    loss = (ce * w / tgt_len[sample_idx]).sum() / b
    return loss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["ar", "diff"], required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seq-len", type=int, default=2048)
    ap.add_argument("--micro-batch", type=int, default=1)
    ap.add_argument("--grad-accum", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--max-steps", type=int, default=200)
    ap.add_argument("--lora-r", type=int, default=32)
    ap.add_argument("--lora-alpha", type=int, default=64)
    ap.add_argument("--save-every", type=int, default=100)
    ap.add_argument("--log-every", type=int, default=5)
    ap.add_argument("--quant", choices=["nf4", "bf16"], default="nf4",
                    help="nf4 = 4-bit QLoRA (12GB cards); bf16 = full-precision LoRA (A100 80GB)")
    ap.add_argument("--grad-checkpoint", action="store_true",
                    help="enable gradient checkpointing (saves VRAM, ~30%% slower); off by default")
    ap.add_argument("--bucket", action="store_true",
                    help="length-bucketed batching to cut padding waste at micro-batch > 1")
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    model_id = args.model or DEFAULT_MODELS[args.arm]
    os.makedirs(args.out, exist_ok=True)
    json.dump(vars(args) | {"model_id": model_id},
              open(os.path.join(args.out, "run_config.json"), "w"), indent=1)

    from transformers import AutoTokenizer, AutoModel, AutoModelForCausalLM, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    tok = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    cls = AutoModelForCausalLM if args.arm == "ar" else AutoModel
    if args.quant == "nf4":
        bnb = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
        model = cls.from_pretrained(
            model_id, quantization_config=bnb, trust_remote_code=True,
            torch_dtype=torch.bfloat16, attn_implementation="sdpa")
        model = prepare_model_for_kbit_training(
            model, use_gradient_checkpointing=args.grad_checkpoint)
    else:  # bf16 full-precision LoRA (A100 80GB)
        model = cls.from_pretrained(
            model_id, trust_remote_code=True, device_map="cuda",
            torch_dtype=torch.bfloat16, attn_implementation="sdpa")
        if args.grad_checkpoint:
            model.gradient_checkpointing_enable()
            model.enable_input_require_grads()

    lcfg = LoraConfig(
        r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        task_type="CAUSAL_LM" if args.arm == "ar" else None)
    model = get_peft_model(model, lcfg)
    model.print_trainable_parameters()

    mask_id = None
    if args.arm == "diff":
        mask_id = getattr(model.config, "mask_token_id", None)
        if mask_id is None and tok.mask_token_id is not None:
            mask_id = tok.mask_token_id
        assert mask_id is not None, "no mask token id found for diffusion model"
        print("mask_token_id:", mask_id)

    pad_id = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
    ds = PairDataset(args.data, tok, args.seq_len, args.arm)
    if args.bucket:
        lengths = [len(p) + len(t) for p, t in ds.rows]
        dl = DataLoader(ds, collate_fn=lambda b: collate(b, pad_id),
                        batch_sampler=BucketBatchSampler(lengths, args.micro_batch, args.seed))
    else:
        dl = DataLoader(ds, batch_size=args.micro_batch, shuffle=True,
                        collate_fn=lambda b: collate(b, pad_id),
                        generator=torch.Generator().manual_seed(args.seed))

    opt = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=args.lr,
        betas=(0.9, 0.95), weight_decay=0.01)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, s / 20) * (0.5 * (1 + math.cos(math.pi * min(1.0, s / args.max_steps)))))

    model.train()
    device = next(model.parameters()).device
    log_path = os.path.join(args.out, "train_log.jsonl")
    step = micro = 0
    acc_loss = 0.0
    steps_in_window = 0
    t0 = time.time()
    done = False
    while not done:
        for batch in dl:
            input_ids, plens, tlens = (x.to(device) for x in batch)
            if args.arm == "ar":
                loss = ar_loss(model, input_ids, plens, tlens, pad_id)
            else:
                loss = diff_loss(model, input_ids, plens, tlens, mask_id)
            (loss / args.grad_accum).backward()
            acc_loss += loss.item()
            micro += 1
            if micro % args.grad_accum == 0:
                torch.nn.utils.clip_grad_norm_(
                    [p for p in model.parameters() if p.requires_grad], 1.0)
                opt.step()
                sched.step()
                opt.zero_grad(set_to_none=True)
                step += 1
                steps_in_window += 1
                if step % args.log_every == 0 or step == 1:
                    vram = torch.cuda.max_memory_allocated() / 2**30
                    rec = {"step": step,
                           "loss": round(acc_loss / args.grad_accum / steps_in_window, 4),
                           "lr": sched.get_last_lr()[0], "vram_peak_gb": round(vram, 2),
                           "elapsed_s": round(time.time() - t0, 1)}
                    print(rec)
                    with open(log_path, "a") as f:
                        f.write(json.dumps(rec) + "\n")
                    acc_loss = 0.0
                    steps_in_window = 0
                if step % args.save_every == 0 or step >= args.max_steps:
                    model.save_pretrained(os.path.join(args.out, f"step{step}"))
                if step >= args.max_steps:
                    done = True
                    break
        if len(ds) == 0:
            raise RuntimeError("empty dataset")

    print("final vram peak GB:", round(torch.cuda.max_memory_allocated() / 2**30, 2))


if __name__ == "__main__":
    main()
