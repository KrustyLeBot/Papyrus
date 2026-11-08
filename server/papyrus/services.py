"""Wires the parts together, once per process (the server and the CLI commands share it)."""
from __future__ import annotations

from pathlib import Path

from .config import Config
from .db import Database
from .embed import Embedder
from .grouping import Grouper
from .htpasswd import Accounts
from .library import Library
from .pipeline import Processor, Worker
from .rules import RulesStore
from .search import Searcher, VectorIndex


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
        self.grouper = Grouper(self.db, self.rules, config.group_threshold, config.knn_threshold)
        self.library = Library(config, self.db, self.grouper)
        self.processor = Processor(config, self.db, self.rules, self.embedder, self.grouper)
        self.vectors = VectorIndex(self.db, idle_seconds=max(config.embed_idle_unload_min, 1) * 60)
        self.searcher = Searcher(self.db, self.embedder, self.vectors)
        self.worker = Worker(self.processor, config, self.db)
        self.accounts = None if config.auth_disabled else Accounts(config.htpasswd_path)
        with self.db.tx():
            self.grouper.inbox_id()
        if start_worker:
            self.library.clean_tmp()
            self.worker.start()

    def close(self) -> None:
        self.worker.stop()
        self.db.close()
