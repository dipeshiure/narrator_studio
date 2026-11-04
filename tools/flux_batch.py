"""Generate many images with one FLUX.1-schnell load (runs in tools/mflux_venv).

    tools/mflux_venv/bin/python tools/flux_batch.py jobs.json

jobs.json: {"family": "flux1" | "flux2", "model_path": "tools/models/...", "width": 960, "height": 544,
            "steps": 4, "images": [{"prompt": "...", "out": "/abs/a.png", "seed": 1}, ...]}
Images whose file already exists are skipped (cache). Progress: "IMG <done> <total>".
"""
import json
import sys
from pathlib import Path

import gc

import mlx.core as mx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memguard import gpu_lock, unload_llms, wait_for_memory  # noqa: E402
from mflux.models.common.config import ModelConfig
from mflux.models.flux.variants.txt2img.flux import Flux1


def main(path):
    job = json.loads(Path(path).read_text())
    todo = [im for im in job["images"] if not Path(im["out"]).exists()]
    total = len(job["images"])
    print(f"IMG {total - len(todo)} {total}", flush=True)
    if not todo:
        return 0
    unload_llms()                            # the local LLM can hold 4+ GB: never share memory with it
    mx.set_cache_limit(512 * 1024 * 1024)   # keep at most 0.5 GB of freed GPU buffers around
    if job.get("family") == "flux2":
        # FLUX.2 [klein] 4B (Apache-2.0): ~10 s an image at 960x544 / 4 steps on the M5,
        # peak ~5 GB. Its text encoder re-reads every prompt, so it stays loaded.
        from mflux.models.common.config.model_config import AVAILABLE_MODELS
        from mflux.models.flux2.variants import Flux2Klein
        flux = Flux2Klein(model_config=AVAILABLE_MODELS["flux2-klein-4b"], model_path=job.get("model_path"))
        draw = lambda im: flux.generate_image(seed=int(im["seed"]), prompt=im["prompt"],
                                              num_inference_steps=int(job.get("steps", 4)), guidance=1.0,
                                              width=int(job["width"]), height=int(job["height"]))
    else:
        flux = Flux1(model_config=ModelConfig.schnell(), model_path=job.get("model_path"))
        # low-RAM: read every prompt first (prompt cache), then drop the text encoders (T5 + CLIP,
        # ~2.5 GB) so only the image model stays in memory while drawing. Measured on the M5:
        # 9.5 GB -> 4.7 GB at peak, no swap, same image.
        from mflux.models.flux.model.flux_text_encoder.prompt_encoder import PromptEncoder
        for im in todo:
            PromptEncoder.encode_prompt(prompt=im["prompt"], prompt_cache=flux.prompt_cache,
                                        t5_tokenizer=flux.tokenizers["t5"], clip_tokenizer=flux.tokenizers["clip"],
                                        t5_text_encoder=flux.t5_text_encoder, clip_text_encoder=flux.clip_text_encoder)
        mx.eval([v for pair in flux.prompt_cache.values() for v in pair])
        flux.t5_text_encoder = None
        flux.clip_text_encoder = None
        gc.collect()
        mx.clear_cache()
        draw = lambda im: flux.generate_image(seed=int(im["seed"]), prompt=im["prompt"],
                                              num_inference_steps=int(job.get("steps", 4)),
                                              width=int(job["width"]), height=int(job["height"]))
    done = total - len(todo)
    for im in todo:
        wait_for_memory(mx.clear_cache)          # never draw while the Mac would have to swap
        image = draw(im)
        out = Path(im["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.stem + ".part.png")
        image.save(path=str(tmp))
        tmp.replace(out)                 # a half-written file never counts as cached
        del image                        # give the GPU scratch memory back after every image:
        gc.collect()                     # it otherwise piles up and pushes the Mac into swap
        mx.clear_cache()
        done += 1
        print(f"IMG {done} {total}", flush=True)
    return 0


if __name__ == "__main__":
    with gpu_lock():                     # never alongside Dia (or another FLUX run)
        raise SystemExit(main(sys.argv[1]))
