"""Sentence embeddings with intfloat/multilingual-e5-small (ONNX, int8 quantised, Xenova's export).

The model is loaded on first use and dropped after EMBED_IDLE_UNLOAD_MIN minutes without work, so the idle
footprint stays small. Without the model files (download failed, offline mirror missing) Papyrus keeps working:
full-text search only, grouping by rules and identifiers only."""
from __future__ import annotations

import ctypes
import gc
import hashlib
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

# Two profiles of the same family (multilingual-e5, ONNX int8, Xenova's export). `small` is the one for a NAS without AVX
# (J3455: ~120 MB, 384 dimensions); `base` is for a machine with AVX2 and RAM to spare (~280 MB on disk, 768 dimensions,
# noticeably better at telling close documents apart). `semantic_min` is the default "similarité minimale" measured
# for each (see README, Measurements): the scores of a model are not comparable with another's.
MODELS = {
    "small": {
        "repo": "Xenova/multilingual-e5-small", "revision": "761b726dd34fb83930e26aab4e9ac3899aa1fa78", "folder": "e5-small",
        "dim": 384, "semantic_min": 0.86,
        "files": {
            "model_quantized.onnx": ("onnx/model_quantized.onnx", "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193"),
            "tokenizer.json": ("tokenizer.json", "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"),
        },
    },
    "base": {
        "repo": "Xenova/multilingual-e5-base", "revision": "1ec9243030a27d1a115d5c340572074c125b58b2", "folder": "e5-base",
        "dim": 768, "semantic_min": 0.84,
        "files": {
            "model_quantized.onnx": ("onnx/model_quantized.onnx", "df7a9a29309e3ad491e1783adf8baee710262cc06079c7cbab63c630277fac94"),
            "tokenizer.json": ("tokenizer.json", "62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626"),
        },
    },
}
DEFAULT_MODEL = "small"
FILES = MODELS["small"]["files"]  # kept for callers that only know the small model
MAX_TOKENS = 512
CHUNK_CHARS = 1600
MAX_CHUNKS = 3


def _trim_memory() -> None:
    """Give freed pages back to the system (glibc keeps them otherwise)."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# Run in a child process before the first in-process load: an onnxruntime build that needs instructions the CPU
# lacks (AVX on a Celeron J3455, say) dies with SIGILL, which no exception handler can catch. Isolated, it only
# turns the model off (degraded mode) instead of killing the server.
PROBE = (
    "import sys, numpy as np, onnxruntime as ort\n"
    "o = ort.SessionOptions(); o.intra_op_num_threads = 1\n"
    "s = ort.InferenceSession(sys.argv[1], o, providers=['CPUExecutionProvider'])\n"
    "names = [i.name for i in s.get_inputs()]\n"
    "ids = np.array([[0, 6, 5, 2]], dtype=np.int64)\n"
    "feed = {'input_ids': ids, 'attention_mask': np.ones_like(ids), 'token_type_ids': np.zeros_like(ids)}\n"
    "s.run(None, {k: v for k, v in feed.items() if k in names})\n"
)


def _trim_memory() -> None:
    """Give freed pages back to the system (glibc keeps them otherwise)."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Embedder:
    def __init__(self, model_dirs: list[Path], model_url: str = "", threads: int = 2, idle_minutes: int = 15,
                 download_dir: Path | None = None, model: str = DEFAULT_MODEL):
        if model not in MODELS:
            raise SystemExit(f"papyrus: EMBED_MODEL={model!r} is unknown (choose {' or '.join(MODELS)})")
        self.name = model
        self.spec = MODELS[model]
        self.dim = self.spec["dim"]
        self.model_dirs = model_dirs
        self.download_dir = download_dir or model_dirs[-1]
        self.model_url = model_url.rstrip("/")
        self.threads = threads
        self.idle_seconds = idle_minutes * 60
        self._lock = threading.RLock()
        self._session = None
        self._tokenizer = None
        self._inputs: list[str] = []
        self._last_used = 0.0
        self.error: str | None = None
        self.downloading = False
        self.incompatible = False  # onnxruntime cannot run on this CPU
        self._probed = False
        self.version = 0  # bumped when the model becomes available (the worker then embeds what was skipped)
        if self.idle_seconds:
            threading.Thread(target=self._unloader, name="embed-unload", daemon=True).start()

    # ---------------------------------------------------------------- files

    def model_dir(self) -> Path | None:
        """The folder holding this model's files: <dir>/<folder> (e5-small, e5-base), and for the small model also <dir>
        itself, where the first versions put them."""
        files = self.spec["files"]
        for d in self.model_dirs:
            for candidate in ([d / self.spec["folder"]] + ([d] if self.name == "small" else [])):
                if all((candidate / name).is_file() for name in files):
                    return candidate
        return None

    @property
    def available(self) -> bool:
        return not self.incompatible and self.model_dir() is not None

    def _probe(self, directory: Path) -> None:
        if self._probed:
            return
        try:
            done = subprocess.run([sys.executable, "-c", PROBE, str(directory / "model_quantized.onnx")],
                                  capture_output=True, timeout=180)
        except (subprocess.TimeoutExpired, OSError) as error:
            raise RuntimeError(f"test d'onnxruntime impossible ({error.__class__.__name__})") from None
        if done.returncode != 0:
            self.incompatible = True
            reason = "plantage (instruction CPU manquante ?)" if done.returncode < 0 else done.stderr.decode("utf-8", "replace").strip()[-200:]
            self.error = f"onnxruntime inutilisable sur ce processeur : {reason}"
            print(f"papyrus: {self.error}", flush=True)
            raise RuntimeError(self.error)
        self._probed = True

    def status(self) -> str:
        if self._session is not None:
            return "loaded"
        if self.downloading:
            return "downloading"
        if self.available:
            return "unloaded"
        return "absent"

    def download(self) -> bool:
        """Fetches the model (MODEL_URL, a mirror holding the same layout, else Hugging Face at a pinned revision),
        checks every file's SHA-256, and only then moves it in place."""
        if self.available:
            return True
        spec = self.spec
        base = self.model_url or f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}"
        target = self.download_dir / spec["folder"]
        self.downloading = True
        try:
            target.mkdir(parents=True, exist_ok=True)
            for name, (remote, sha) in spec["files"].items():
                final = target / name
                if final.is_file() and _sha256(final) == sha:
                    continue
                part = target / (name + ".part")
                url = f"{base}/{remote}"
                print(f"papyrus: downloading {url}", flush=True)
                request = urllib.request.Request(url, headers={"User-Agent": "papyrus"})
                with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as out:
                    while True:
                        block = response.read(1 << 20)
                        if not block:
                            break
                        out.write(block)
                if _sha256(part) != sha:
                    part.unlink(missing_ok=True)
                    raise RuntimeError(f"{name}: empreinte SHA-256 inattendue")
                part.replace(final)
            self.error = None
            self.version += 1
            print(f"papyrus: embeddings model {self.name} ready", flush=True)
            return True
        except Exception as error:  # noqa: BLE001 - offline, mirror missing… the app runs in degraded mode
            self.error = f"modèle d'embeddings indisponible : {error}"
            print(f"papyrus: {self.error}", flush=True)
            return False
        finally:
            self.downloading = False

    # ---------------------------------------------------------------- session

    def _load(self) -> None:
        directory = self.model_dir()
        if directory is None:
            raise RuntimeError("modèle d'embeddings absent")
        self._probe(directory)
        import onnxruntime as ort
        from tokenizers import Tokenizer

        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(str(directory / "model_quantized.onnx"), options,
                                             providers=["CPUExecutionProvider"])
        self._inputs = [i.name for i in self._session.get_inputs()]
        tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        tokenizer.enable_truncation(MAX_TOKENS)
        tokenizer.enable_padding(pad_id=tokenizer.token_to_id("<pad>") or 1, pad_token="<pad>")
        self._tokenizer = tokenizer
        print("papyrus: embeddings model loaded", flush=True)

    def unload(self) -> None:
        with self._lock:
            if self._session is None:
                return
            self._session = None
            self._tokenizer = None
        _trim_memory()
        print("papyrus: embeddings model unloaded (idle)", flush=True)

    def _unloader(self) -> None:
        while True:
            time.sleep(30)
            if self._session is not None and time.monotonic() - self._last_used > self.idle_seconds:
                self.unload()

    def _run(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            if self._session is None:
                self._load()
            self._last_used = time.monotonic()
            encodings = self._tokenizer.encode_batch(texts)
            ids = np.array([e.ids for e in encodings], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            feed = {k: v for k, v in feed.items() if k in self._inputs}
            hidden = self._session.run(None, feed)[0]
            self._last_used = time.monotonic()
        weights = mask[:, :, None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        return pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)

    # ---------------------------------------------------------------- public

    def embed_query(self, query: str) -> np.ndarray:
        return self._run([f"query: {query.strip()}"])[0].astype(np.float32)

    def embed_document(self, title: str, text: str) -> np.ndarray:
        """Title plus the start of the text; a long document averages up to three chunks of it."""
        body = " ".join(text.split())
        chunks = [body[i:i + CHUNK_CHARS] for i in range(0, min(len(body), CHUNK_CHARS * MAX_CHUNKS), CHUNK_CHARS)] or [""]
        vectors = self._run([f"passage: {title}\n{chunk}" for chunk in chunks])
        mean = vectors.mean(axis=0)
        return (mean / max(float(np.linalg.norm(mean)), 1e-12)).astype(np.float32)
