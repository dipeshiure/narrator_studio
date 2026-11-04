# Dia-1.6B-0626 (try later, on Wi-Fi)

The download was stopped on purpose (user: only on Wi-Fi). A partial first shard (about 70 MB) is in
`tools/models/dia-0626-src/`; the command below resumes it.

    cd tools/models/dia-0626-src
    for f in model-00001-of-00002.safetensors model-00002-of-00002.safetensors; do
      curl -L -C - --retry 3 -o $f "https://huggingface.co/nari-labs/Dia-1.6B-0626/resolve/main/$f"; done

(About 6.4 GB. The huggingface_hub downloader failed with a CAS error; plain curl with -C - resumes.)
Then convert and try it:

    tools/dia_venv/bin/python tools/convert_dia_hf.py tools/models/dia-0626-src tools/models/dia-0626-mlx

`convert_dia_hf.py` is written but untested (the key mapping from the Hugging Face layout to the MLX one
is checked only by listening to the result). dia_batch.py still needs a way to point at that folder.
