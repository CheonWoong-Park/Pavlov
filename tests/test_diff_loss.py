"""Unit tests for the masked-diffusion SFT loss in train_lora.py.

Uses a stub model returning fixed logits so the test runs without GPU or
model weights. Checks: (1) only target tokens are ever masked, (2) every
sample contributes >=1 masked token, (3) loss is finite and positive,
(4) a model that puts all probability on the correct token drives the
loss to ~0, (5) determinism under fixed torch seed.
"""

import os
import sys
import types

import torch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from train_lora import diff_loss  # noqa: E402

VOCAB = 64
MASK_ID = 63


class StubModel:
    """Records the noisy input it was called with; returns uniform logits."""

    def __init__(self, oracle=False):
        self.oracle = oracle
        self.last_input = None

    def __call__(self, input_ids=None, attention_mask=None):
        self.last_input = input_ids.clone()
        b, L = input_ids.shape
        if self.oracle:
            # near-one-hot on a fixed "correct" pattern filled in by the test
            logits = torch.full((b, L, VOCAB), -30.0)
            logits.scatter_(2, self.correct.unsqueeze(-1), 30.0)
        else:
            logits = torch.zeros(b, L, VOCAB)
        return types.SimpleNamespace(logits=logits)


def make_batch(b=4, L=32, seed=0):
    g = torch.Generator().manual_seed(seed)
    input_ids = torch.randint(0, VOCAB - 1, (b, L), generator=g)
    prompt_lens = torch.tensor([5, 10, 1, 20])
    total_lens = torch.tensor([30, 32, 8, 21])  # sample 3: single target token
    return input_ids, prompt_lens, total_lens


def test_mask_only_targets():
    torch.manual_seed(0)
    input_ids, plens, tlens = make_batch()
    m = StubModel()
    diff_loss(m, input_ids, plens, tlens, MASK_ID)
    noisy = m.last_input
    changed = noisy != input_ids
    pos = torch.arange(input_ids.shape[1])[None, :]
    is_target = (pos >= plens[:, None]) & (pos < tlens[:, None])
    assert (noisy[changed] == MASK_ID).all(), "changed tokens must become MASK"
    assert not (changed & ~is_target).any(), "prompt/pad tokens must never be masked"


def test_min_one_mask_per_sample():
    for seed in range(20):
        torch.manual_seed(seed)
        input_ids, plens, tlens = make_batch(seed=seed)
        m = StubModel()
        diff_loss(m, input_ids, plens, tlens, MASK_ID)
        changed = (m.last_input != input_ids).any(dim=1)
        assert changed.all(), f"seed {seed}: some sample had zero masked tokens"


def test_loss_finite_positive():
    torch.manual_seed(1)
    input_ids, plens, tlens = make_batch()
    loss = diff_loss(StubModel(), input_ids, plens, tlens, MASK_ID)
    assert torch.isfinite(loss) and loss.item() > 0


def test_oracle_drives_loss_to_zero():
    torch.manual_seed(2)
    input_ids, plens, tlens = make_batch()
    m = StubModel(oracle=True)
    m.correct = input_ids
    loss = diff_loss(m, input_ids, plens, tlens, MASK_ID)
    assert loss.item() < 1e-3, f"oracle loss should be ~0, got {loss.item()}"


def test_deterministic_under_seed():
    vals = []
    for _ in range(2):
        torch.manual_seed(7)
        input_ids, plens, tlens = make_batch()
        vals.append(diff_loss(StubModel(), input_ids, plens, tlens, MASK_ID).item())
    assert vals[0] == vals[1]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
