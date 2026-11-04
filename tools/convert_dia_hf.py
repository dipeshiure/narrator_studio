"""Convert nari-labs/Dia-1.6B-0626 (Hugging Face "transformers" layout) into the layout mlx-audio's
Dia loads (the one mlx-community/Dia-1.6B-* uses).

    tools/dia_venv/bin/python tools/convert_dia_hf.py tools/models/dia-0626-src tools/models/dia-0626-mlx

Weights are written as bfloat16 next to mlx-community's config.json. Use it by pointing
dia_batch.py at the folder (job "model": "/abs/path", or env DIA_MODEL).
"""
import glob
import json
import shutil
import sys
from pathlib import Path

import mlx.core as mx

NCH, VOC = 9, 1028


def convert(src, dst):
    src, dst = Path(src), Path(dst)
    hf = {}
    for f in sorted(glob.glob(str(src / "*.safetensors"))):
        hf.update(mx.load(f))
    out = {}

    def lin_in(w, heads, dim):       # [heads*dim, in] -> (in, heads, dim)
        return w.T.reshape(w.shape[1], heads, dim)

    def lin_out(w, heads, dim):      # [hidden, heads*dim] -> (heads, dim, hidden)
        return w.T.reshape(heads, dim, w.shape[0])

    for part, cfg in (("encoder", dict(h=16, kv=16, d=128)), ("decoder", dict(h=16, kv=4, d=128))):
        n = 1 + max(int(k.split(".")[3]) for k in hf if k.startswith(f"model.{part}.layers."))
        for i in range(n):
            P, Q = f"model.{part}.layers.{i}.", f"{part}.layers.{i}."
            for norm in ("pre_sa_norm", "post_sa_norm", "pre_ca_norm", "pre_mlp_norm"):
                if P + norm + ".weight" in hf:
                    out[Q + norm + ".weight"] = hf[P + norm + ".weight"]
            attns = ["self_attention"] + (["cross_attention"] if part == "decoder" else [])
            for a in attns:
                kv = cfg["kv"] if a == "self_attention" else 16
                out[Q + a + ".q_proj.weight"] = lin_in(hf[P + a + ".q_proj.weight"], cfg["h"], cfg["d"])
                out[Q + a + ".k_proj.weight"] = lin_in(hf[P + a + ".k_proj.weight"], kv, cfg["d"])
                out[Q + a + ".v_proj.weight"] = lin_in(hf[P + a + ".v_proj.weight"], kv, cfg["d"])
                out[Q + a + ".o_proj.weight"] = lin_out(hf[P + a + ".o_proj.weight"], cfg["h"], cfg["d"])
            gu = hf[P + "mlp.gate_up_proj.weight"]            # [2*inter, in], gate first
            out[Q + "mlp.wi_fused.weight"] = gu.T.reshape(gu.shape[1], 2, gu.shape[0] // 2)
            out[Q + "mlp.wo.weight"] = hf[P + "mlp.down_proj.weight"].T
        out[f"{part}.norm.weight"] = hf[f"model.{part}.norm.weight"]
    out["encoder.embedding.weight"] = hf["model.encoder.embedding.weight"]
    emb = hf["model.decoder.embeddings.embed.weight"]         # one table, channel c at rows c*1028..
    for c in range(NCH):
        out[f"decoder.embeddings.{c}.weight"] = emb[c * VOC:(c + 1) * VOC]
    lg = hf["logits_dense.weight"]                            # [9*1028, 2048] -> (2048, 9, 1028)
    out["decoder.logits_dense.weight"] = lg.T.reshape(lg.shape[1], NCH, VOC)
    out = {k: v.astype(mx.bfloat16) for k, v in out.items()}
    dst.mkdir(parents=True, exist_ok=True)
    mx.save_safetensors(str(dst / "model.safetensors"), out)
    ref = Path.home() / ".cache/huggingface/hub/models--mlx-community--Dia-1.6B-4bit/snapshots"
    cfg = next(ref.glob("*/config.json"))
    shutil.copy(cfg, dst / "config.json")
    print(f"{len(out)} tensors -> {dst}")


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2])
