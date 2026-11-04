"""Faster Dia decoding on Apple Silicon (experimental): swaps mlx-audio's per-token decode step for one
compiled graph with fixed-size caches.

What changes (the model, its weights and the sampling are untouched):
  * the self-attention cache is preallocated (T fixed) and written in place with slice_update, instead
    of growing every step; positions after the current one are masked;
  * the cache keeps the 4 key/value heads (not 16 repeated copies); mx.fast.scaled_dot_product_attention
    handles the grouping;
  * the whole step (18 layers + logits) is one mx.compile'd function, so Python only launches it once.

    import dia_fast; dia_fast.install(model)   # then call generate as usual (set model._fast_extra first)
"""
import mlx.core as mx

_STATE = {}


def _rope(x, pos, ts):
    # x: (B, 1, heads, H), pos: (B, 1) -> same math as layers.RotaryEmbedding
    ang = pos[..., None, None].astype(mx.float32) / ts
    sin, cos = mx.sin(ang).astype(x.dtype), mx.cos(ang).astype(x.dtype)
    h = x.shape[-1] // 2
    a, b = x[..., :h], x[..., h:]
    return mx.concatenate([a * cos - b * sin, b * cos + a * sin], axis=-1)


def _pack(w2, bits, gs=64):
    """w2: (out, in) -> quantized (w, scales, biases) or the plain matrix when bits == 0."""
    if not bits:
        return (w2,)
    return mx.quantize(w2, group_size=gs, bits=bits)


def _lin(x, pk, bits, gs=64):
    if not bits:
        return x @ pk[0].T
    return mx.quantized_matmul(x, *pk, transpose=True, group_size=gs, bits=bits)


def install(model, bits=0):
    """bits: 0 = keep bf16 weights; 8 or 4 = decode with quantized matmuls (less memory read per token)."""
    dec = model.model.decoder
    layers = dec.layers
    ts = layers[0].self_attention.rotary_emb._timescale
    n_layers = len(layers)
    W = []                                             # per layer: packed projections
    for L in layers:
        sa, ca, ml = L.self_attention, L.cross_attention, L.mlp
        flat = lambda w: w.reshape(w.shape[0], -1).T      # (in, a, b) -> (a*b, in)
        W.append(dict(
            q=_pack(flat(sa.q_proj.weight), bits), k=_pack(flat(sa.k_proj.weight), bits), v=_pack(flat(sa.v_proj.weight), bits),
            o=_pack(sa.o_proj.weight.reshape(-1, sa.o_proj.weight.shape[-1]).T, bits),
            cq=_pack(flat(ca.q_proj.weight), bits),
            co=_pack(ca.o_proj.weight.reshape(-1, ca.o_proj.weight.shape[-1]).T, bits),
            wi=_pack(flat(ml.wi_fused.weight), bits), wo=_pack(ml.wo.weight.T, bits)))
    lw = dec.logits_dense.weight
    WL = _pack(lw.reshape(lw.shape[0], -1).T, bits)
    mx.eval([v for d_ in W for pk in d_.values() for v in pk], list(WL))
    nh, nkv, hd = 16, 4, 128

    def step(ids, pos, kc, vc, ck, cv, cmask):
        # ids (2,1,9) int32, pos (2,1) int32, kc/vc lists of (2,4,T,128), ck/cv lists of (2,16,S,128),
        # cmask (2,1,1,S) additive
        x = None
        for i in range(dec.num_channels):
            e = dec.embeddings[i](ids[..., i])
            x = e if x is None else x + e
        T = kc[0].shape[2]
        smask = mx.where(mx.arange(T)[None, None, None, :] <= pos[:, None, :, None], 0.0, -1e9).astype(x.dtype)
        start = pos[0, 0:1].astype(mx.int32)
        new_k, new_v = [], []
        for i, layer in enumerate(layers):
            sa, ca = layer.self_attention, layer.cross_attention
            h = layer.pre_sa_norm(x)
            w = W[i]
            q = _rope(_lin(h, w["q"], bits).reshape(2, 1, nh, hd), pos, ts).transpose(0, 2, 1, 3)    # (2,16,1,128)
            k = _rope(_lin(h, w["k"], bits).reshape(2, 1, nkv, hd), pos, ts).transpose(0, 2, 1, 3)   # (2,4,1,128)
            v = _lin(h, w["v"], bits).reshape(2, 1, nkv, hd).transpose(0, 2, 1, 3)
            kk = mx.slice_update(kc[i], k, start, axes=(2,))
            vv = mx.slice_update(vc[i], v, start, axes=(2,))
            new_k.append(kk); new_v.append(vv)
            o = mx.fast.scaled_dot_product_attention(q, kk, vv, scale=1.0, mask=smask)
            x = x + _lin(o.transpose(0, 2, 1, 3).reshape(2, 1, nh * hd), w["o"], bits)
            h = layer.pre_ca_norm(x)
            q = _rope(_lin(h, w["cq"], bits).reshape(2, 1, nh, hd), pos, ts).transpose(0, 2, 1, 3)
            o = mx.fast.scaled_dot_product_attention(q, ck[i], cv[i], scale=1.0, mask=cmask.astype(x.dtype))
            x = x + _lin(o.transpose(0, 2, 1, 3).reshape(2, 1, nh * hd), w["co"], bits)
            y = _lin(layer.pre_mlp_norm(x), w["wi"], bits)
            inter = y.shape[-1] // 2
            x = x + _lin(mx.sigmoid(y[..., :inter]) * y[..., :inter] * y[..., inter:], w["wo"], bits)
        logits = _lin(dec.norm(x), WL, bits).reshape(2, 1, dec.num_channels, -1)
        return logits.astype(mx.float32), new_k, new_v

    compiled = mx.compile(step)

    def decode_step(tgt_ids_Bx1xC, tgt_pos_Bx1, encoder_out, self_attn_mask, cross_attn_mask,
                    self_attention_cache, cross_attention_cache):
        key = id(self_attention_cache)
        st = _STATE.get("cur")
        if st is None or st["key"] != key:                                      # a new generation
            keys0 = self_attention_cache[0].keys
            extra = int(getattr(model, "_fast_extra", 1800))
            T = int(tgt_pos_Bx1[0, 0].item()) + extra
            kc, vc = [], []
            for c in self_attention_cache:
                used = c.offset
                k4 = c.keys[:, ::4, :used]                                      # undo the head repeat
                v4 = c.values[:, ::4, :used]
                pad = mx.zeros((k4.shape[0], k4.shape[1], T - used, k4.shape[3]), dtype=k4.dtype)
                kc.append(mx.concatenate([k4, pad], axis=2)); vc.append(mx.concatenate([v4, pad], axis=2))
            cmask = mx.where(cross_attn_mask, 0.0, -1e9)
            st = _STATE["cur"] = {"key": key, "hold": self_attention_cache, "kc": kc, "vc": vc, "cmask": cmask,
                                  "ck": [c.k for c in cross_attention_cache], "cv": [c.v for c in cross_attention_cache]}
            mx.eval(kc, vc)
        logits, st["kc"], st["vc"] = compiled(tgt_ids_Bx1xC.astype(mx.int32), tgt_pos_Bx1.astype(mx.int32),
                                              st["kc"], st["vc"], st["ck"], st["cv"], st["cmask"])
        return logits

    dec._orig_decode_step = dec.decode_step
    dec.decode_step = decode_step
    return model


def uninstall(model):
    dec = model.model.decoder
    if hasattr(dec, "_orig_decode_step"):
        dec.decode_step = dec._orig_decode_step
        _STATE.pop("cur", None)
