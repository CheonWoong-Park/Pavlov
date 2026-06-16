"""Stage-1 skeleton generation for evaluation (plan §6).

Loads a frozen base model + a trained LoRA adapter and generates an anonymized
skeleton for each decompile-eval item (input = Ghidra pseudocode). Two arms:

  --arm ar    Qwen2.5-Coder-7B + adapter, standard autoregressive decoding
  --arm diff  DiffuCoder-7B + adapter, DreamModel.diffusion_generate (iterative
              denoising); --steps controls the denoising-step / quality trade-off

Output: one jsonl line per item
  {"task_id", "type", "skeleton": <generated text>}

Prompt format is identical to training (train_lora.PROMPT_TMPL) so the adapter
sees the same distribution. Run locally (5070, --quant nf4) or on a GPU box.
"""

import argparse
import json
import os
import time

import torch

from train_lora import PROMPT_TMPL, DEFAULT_MODELS


def load_model(arm, adapter, quant):
    from transformers import AutoTokenizer, AutoModel, AutoModelForCausalLM, BitsAndBytesConfig
    from peft import PeftModel

    model_id = DEFAULT_MODELS[arm]
    tok = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    cls = AutoModelForCausalLM if arm == "ar" else AutoModel
    kw = dict(trust_remote_code=True, torch_dtype=torch.bfloat16, attn_implementation="sdpa")
    if quant == "nf4":
        kw["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    else:
        kw["device_map"] = "cuda"
    model = cls.from_pretrained(model_id, **kw)
    model = PeftModel.from_pretrained(model, adapter)
    model.eval()
    return model, tok


@torch.no_grad()
def gen_ar(model, tok, prompt, max_new_tokens):
    ids = tok(prompt, return_tensors="pt").to(model.device)
    out = model.generate(**ids, max_new_tokens=max_new_tokens, do_sample=False,
                         num_beams=1, pad_token_id=tok.pad_token_id or tok.eos_token_id)
    new = out[0][ids["input_ids"].shape[1]:]
    return tok.decode(new, skip_special_tokens=True)


@torch.no_grad()
def gen_diff(model, tok, prompt, max_new_tokens, steps, temperature, top_p):
    ids = tok(prompt, return_tensors="pt").to(model.device)
    out = model.diffusion_generate(
        ids["input_ids"], attention_mask=ids["attention_mask"],
        max_new_tokens=max_new_tokens, steps=steps,
        temperature=temperature, top_p=top_p, alg="entropy", alg_temp=0.0,
        output_history=False, return_dict_in_generate=True)
    new = out.sequences[0][ids["input_ids"].shape[1]:]
    return tok.decode(new, skip_special_tokens=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["ar", "diff"], required=True)
    ap.add_argument("--adapter", required=True, help="path to LoRA adapter dir (step2000)")
    ap.add_argument("--eval-json", required=True, help="decompile-eval ghidra json")
    ap.add_argument("--out", required=True, help="output jsonl of generated skeletons")
    ap.add_argument("--quant", choices=["bf16", "nf4"], default="nf4")
    ap.add_argument("--max-new-tokens", type=int, default=512)
    ap.add_argument("--steps", type=int, default=256, help="diff arm: denoising steps")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--limit", type=int, default=0, help=">0: only first N items (smoke test)")
    args = ap.parse_args()

    data = json.load(open(args.eval_json))
    if args.limit:
        data = data[:args.limit]
    model, tok = load_model(args.arm, args.adapter, args.quant)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    t0 = time.time()
    with open(args.out, "w") as f:
        for i, item in enumerate(data):
            prompt = PROMPT_TMPL.format(pseudo=item["input_asm_prompt"])
            if args.arm == "ar":
                skel = gen_ar(model, tok, prompt, args.max_new_tokens)
            else:
                skel = gen_diff(model, tok, prompt, args.max_new_tokens,
                                args.steps, args.temperature, args.top_p)
            rec = {"task_id": item["task_id"], "type": item["type"], "skeleton": skel}
            f.write(json.dumps(rec) + "\n")
            f.flush()
            if (i + 1) % 10 == 0 or i == 0:
                dt = time.time() - t0
                print(f"[{i+1}/{len(data)}] {dt:.0f}s elapsed, {dt/(i+1):.1f}s/item", flush=True)
    print(f"done: {len(data)} items -> {args.out} in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
