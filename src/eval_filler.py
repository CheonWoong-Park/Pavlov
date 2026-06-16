"""Stage-2 filler: frozen Qwen2.5-Coder-7B-Instruct fills an anonymized
skeleton back into compilable C.

The filler is never trained and is identical for both arms, so it isolates the
diff-vs-AR comparison to stage 1. It receives the stage-1 skeleton plus the
original Ghidra pseudocode as context, and must restore real identifiers and
literal values while keeping the skeleton's structure. The entry function is
named `func0` to match the decompile-eval test harness.

Input : stage-1 skeletons (eval_generate.py output) + decompile-eval ghidra json
Output: one jsonl line per item  {"task_id", "type", "code"}
"""

import argparse
import json
import os
import time

import torch

FILLER_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"

SYS = "You restore names and literal values in anonymized C code skeletons."

USER_TMPL = """Below are Ghidra decompiler pseudocode and an anonymized skeleton of the same
function. The skeleton keeps the exact control flow and structure but replaces
identifiers with VAR_n / FUNC_n / TYPE_n / FIELD_n / LABEL_n and literals with
INT_LIT / FLOAT_LIT / STR_LIT / CHAR_LIT.

Produce a compilable C function by filling in real identifiers and literal values,
following the skeleton's structure exactly (do not add or remove statements).
Name the entry function `func0`. Output only the C code, no explanation.

### Pseudocode
{pseudo}

### Skeleton
{skeleton}
"""


def load(quant):
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
    tok = AutoTokenizer.from_pretrained(FILLER_MODEL)
    kw = dict(torch_dtype=torch.bfloat16, attn_implementation="sdpa")
    if quant == "nf4":
        kw["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    else:
        kw["device_map"] = "cuda"
    model = AutoModelForCausalLM.from_pretrained(FILLER_MODEL, **kw)
    model.eval()
    return model, tok


def extract_code(text):
    """Strip a markdown ```c ... ``` fence if the model wrapped its output."""
    if "```" in text:
        block = text.split("```", 2)[1]
        if block[:1].lower() == "c" and block[1:2] in ("\n", " "):
            block = block[1:]
        return block.strip()
    return text.strip()


@torch.no_grad()
def fill(model, tok, pseudo, skeleton, max_new_tokens):
    msgs = [{"role": "system", "content": SYS},
            {"role": "user", "content": USER_TMPL.format(pseudo=pseudo, skeleton=skeleton)}]
    prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    ids = tok(prompt, return_tensors="pt").to(model.device)
    out = model.generate(**ids, max_new_tokens=max_new_tokens, do_sample=False,
                         pad_token_id=tok.pad_token_id or tok.eos_token_id)
    new = out[0][ids["input_ids"].shape[1]:]
    return extract_code(tok.decode(new, skip_special_tokens=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", required=True, help="stage-1 skeleton jsonl (eval_generate output)")
    ap.add_argument("--eval-json", required=True, help="decompile-eval ghidra json (pseudocode context)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--quant", choices=["bf16", "nf4"], default="nf4")
    ap.add_argument("--max-new-tokens", type=int, default=768)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    gen = [json.loads(l) for l in open(args.gen)]
    if args.limit:
        gen = gen[:args.limit]
    ev = {(it["task_id"], it["type"]): it for it in json.load(open(args.eval_json))}
    model, tok = load(args.quant)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    t0 = time.time()
    with open(args.out, "w") as f:
        for i, r in enumerate(gen):
            item = ev.get((r["task_id"], r["type"]))
            pseudo = item["input_asm_prompt"] if item else ""
            code = fill(model, tok, pseudo, r["skeleton"], args.max_new_tokens)
            f.write(json.dumps({"task_id": r["task_id"], "type": r["type"], "code": code}) + "\n")
            f.flush()
            if (i + 1) % 10 == 0 or i == 0:
                dt = time.time() - t0
                print(f"[{i+1}/{len(gen)}] {dt:.0f}s, {dt/(i+1):.1f}s/item", flush=True)
    print(f"done: {len(gen)} items -> {args.out} in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
