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
        self.db = Database(config.db_path)
        self.rules = RulesStore(config.rules_path)
        baked = Path("/opt/papyrus/models")
        dirs = [d for d in (baked, config.model_dir) if d]
        self.embedder = Embedder(dirs, config.model_url, config.embed_threads, config.embed_idle_unload_min,
                                 download_dir=config.model_dir)
        self.vault = Vault(self.db)
        self.processor = Processor(config, self.db, self.rules, self.embedder, self.vault)
        self.library = Library(config, self.db, self.processor.source_path, self.vault)
        self.vectors = VectorIndex(self.db, idle_seconds=max(config.embed_idle_unload_min, 1) * 60)
        self.searcher = Searcher(self.db, self.embedder, self.vectors, config.search_defaults)
        self.worker = Worker(self.processor, config, self.db)
        self.scanner = Scanner(config, self.db)
        self.scanner.on_new_work = self.worker.wake
        self.accounts = None if config.auth_disabled else Accounts(config.htpasswd_path)
        if start_worker:
            self.worker.start()
            self.scanner.start()

    def close(self) -> None:
        self.scanner.stop()
        self.worker.stop()
        self.db.close()
