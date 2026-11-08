"""Wires the parts together, once per process (the server and the CLI commands share it)."""
from __future__ import annotations

from pathlib import Path

from .config import Config
from .db import Database
from .embed import Embedder
from .htpasswd import Accounts
from .library import Library
from .pipeline import Processor, Worker
from .rules import RulesStore
from .scanner import Scanner
from .search import Searcher, VectorIndex
from .vault import Vault


class Services:
    def __init__(self, config: Config, start_worker: bool = False):
        self.config = config
        config.ensure_dirs()
        from . import extract

        extract.MEMORY_LIMIT_MB = config.ocr_memory_mb
        self.db = Database(config.db_path, config.journal, config.locking)
        self.rules = RulesStore(config.rules_path)
        baked = Path("/opt/papyrus/models")
        dirs = [d for d in (baked, config.model_dir) if d]
        self.embedder = Embedder(dirs, config.model_url, config.embed_threads, config.embed_idle_unload_min,
                                 download_dir=config.model_dir, model=config.embed_model)
        self.vault = Vault(self.db)
        self.processor = Processor(config, self.db, self.rules, self.embedder, self.vault)
        self.library = Library(config, self.db, self.processor.source_path, self.vault)
        self.vectors = VectorIndex(self.db, idle_seconds=config.embed_idle_unload_min * 60, dim=self.embedder.dim)  # 0 = never
        self._check_model_change()
        self.searcher = Searcher(self.db, self.embedder, self.vectors,
                                 {"semantic_min": self.embedder.spec["semantic_min"], **config.search_defaults})  # each model has its own scale
        self.worker = Worker(self.processor, config, self.db)
        self.scanner = Scanner(config, self.db)
        self.scanner.on_new_work = self.worker.wake
        self.accounts = None if config.auth_disabled else Accounts(config.htpasswd_path)
        if start_worker:
            self.worker.start()
            self.scanner.start()

    def _check_model_change(self) -> None:
        """Embeddings of another model (another size, other scores) are of no use: they are cleared, and the worker
        computes them again with the new model as soon as it is available."""
        saved = self.db.setting("embed_model") or "small"  # databases from before this setting held the small model
        if saved != self.embedder.name:
            with self.db.tx():
                n = self.db.conn.execute("UPDATE documents SET embedding = NULL WHERE embedding IS NOT NULL").rowcount
            print(f"papyrus: embeddings model changed ({saved} → {self.embedder.name}): {n} document(s) will be embedded again", flush=True)
        with self.db.tx():
            self.db.set_setting("embed_model", self.embedder.name)

    def close(self) -> None:
        self.scanner.stop()
        self.worker.stop()
        self.db.close()
