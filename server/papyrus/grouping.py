"""The group tree and the automatic placement of documents (§8).

Placement of one document, for each type it matched:
  1. the type's top-level group (created when missing);
  2. inside it, the sub-group sharing one of the document's keys (siret, address…), keys present in two sibling
     sub-groups being ambiguous and ignored; else the sub-group whose centroid is close enough to the document's
     embedding; else a new sub-group named after the best key. A sub-group whose keys of some kind are all
     different from the document's keys of that kind is never chosen: two employers, two properties stay apart.
Without any type: the k nearest classified documents vote; a strong majority places it ("suggestion appliquée"),
otherwise it lands in « À classer » with three suggestions.

Manual assignments and locked documents are never touched by the automatic placement."""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict

import numpy as np

from .db import Database, blob_to_vec, vec_to_blob
from .rules import Rules, RulesStore, TypeRule
from .text import fold

INBOX_NAME = "À classer"
PALETTE = ["#2FB59B", "#4C8DF6", "#E3A548", "#E86F5C", "#D46BB3", "#7BBE4A", "#B89A6E", "#7C8AA5"]
# keys that identify one thing for sure: a document holding a different one belongs elsewhere
STRONG_KEYS = {"siret", "siren", "che_uid", "tva", "cadastre_ref", "contract_no", "year"}
KNN_K = 5


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Grouper:
    def __init__(self, db: Database, rules: RulesStore, group_threshold: float = 0.88, knn_threshold: float = 0.85):
        self.db = db
        self.rules_store = rules
        self.group_threshold = group_threshold
        self.knn_threshold = knn_threshold

    @property
    def rules(self) -> Rules:
        return self.rules_store.rules

    # ---------------------------------------------------------------- tree helpers

    def inbox_id(self) -> int:
        conn = self.db.conn
        row = conn.execute("SELECT id FROM groups WHERE is_inbox = 1").fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO groups(name, color, is_inbox, created_at) VALUES (?, ?, 1, ?)",
                           (INBOX_NAME, "#9A94AD", now()))
        return cur.lastrowid

    def _next_color(self) -> str:
        used = [r["color"] for r in self.db.conn.execute("SELECT color FROM groups WHERE parent_id IS NULL AND is_inbox = 0")]
        for color in PALETTE:
            if color not in used:
                return color
        return PALETTE[len(used) % len(PALETTE)]

    def type_group(self, rule: TypeRule) -> int:
        conn = self.db.conn
        row = conn.execute("SELECT id FROM groups WHERE parent_id IS NULL AND type_name = ?", (rule.name,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO groups(name, color, type_name, auto, created_at) VALUES (?, ?, ?, 1, ?)",
                           (rule.name, rule.color or self._next_color(), rule.name, now()))
        return cur.lastrowid

    def root_of(self, group_id: int) -> int:
        conn = self.db.conn
        seen = set()
        while group_id not in seen:
            seen.add(group_id)
            row = conn.execute("SELECT parent_id FROM groups WHERE id = ?", (group_id,)).fetchone()
            if not row or row["parent_id"] is None:
                return group_id
            group_id = row["parent_id"]
        return group_id

    def descendants(self, group_id: int) -> list[int]:
        rows = self.db.conn.execute(
            "WITH RECURSIVE t(id) AS (SELECT ? UNION SELECT g.id FROM groups g JOIN t ON g.parent_id = t.id) SELECT id FROM t",
            (group_id,)).fetchall()
        return [r["id"] for r in rows]

    def rule_of_group(self, group_id: int) -> TypeRule | None:
        row = self.db.conn.execute("SELECT type_name FROM groups WHERE id = ?", (self.root_of(group_id),)).fetchone()
        return self.rules.by_name(row["type_name"]) if row and row["type_name"] else None

    # ---------------------------------------------------------------- keys

    def doc_keys(self, doc_id: int) -> list[tuple[str, str, str]]:
        return [(r["key"], r["value"], r["label"]) for r in
                self.db.conn.execute("SELECT key, value, label FROM doc_keys WHERE doc_id = ? ORDER BY rowid", (doc_id,))]

    def _group_keys(self, group_ids: list[int]) -> dict[int, dict[str, set[str]]]:
        out: dict[int, dict[str, set[str]]] = {g: defaultdict(set) for g in group_ids}
        if not group_ids:
            return out
        marks = ",".join("?" * len(group_ids))
        for r in self.db.conn.execute(f"SELECT group_id, key, value FROM group_keys WHERE group_id IN ({marks})", group_ids):
            out[r["group_id"]][r["key"]].add(r["value"])
        return out

    def learn_keys(self, group_id: int, doc_id: int, kinds: list[str] | None, source: str = "auto") -> None:
        blacklist = self.rules.blacklist
        for key, value, label in self.doc_keys(doc_id):
            if kinds is not None and key not in kinds:
                continue
            if (key, fold(value)) in blacklist:
                continue
            self.db.conn.execute(
                "INSERT INTO group_keys(group_id, key, value, label, source) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(group_id, key, value) DO UPDATE SET source = CASE WHEN excluded.source = 'manual' "
                "THEN 'manual' ELSE group_keys.source END",
                (group_id, key, value, label, source))

    # ---------------------------------------------------------------- centroids

    def update_centroids(self, group_ids) -> None:
        conn = self.db.conn
        for gid in set(group_ids):
            rows = conn.execute("SELECT d.embedding FROM doc_groups dg JOIN documents d ON d.id = dg.doc_id "
                                "WHERE dg.group_id = ? AND d.embedding IS NOT NULL", (gid,)).fetchall()
            if not rows:
                conn.execute("UPDATE groups SET centroid = NULL, centroid_n = 0 WHERE id = ?", (gid,))
                continue
            matrix = np.vstack([blob_to_vec(r["embedding"]) for r in rows])
            mean = matrix.mean(axis=0)
            mean /= max(float(np.linalg.norm(mean)), 1e-12)
            conn.execute("UPDATE groups SET centroid = ?, centroid_n = ? WHERE id = ?", (vec_to_blob(mean), len(rows), gid))

    # ---------------------------------------------------------------- assignments

    def _assign(self, doc_id: int, group_id: int, source: str) -> None:
        self.db.conn.execute(
            "INSERT INTO doc_groups(doc_id, group_id, source) VALUES (?, ?, ?) ON CONFLICT(doc_id, group_id) DO UPDATE "
            "SET source = CASE WHEN excluded.source = 'manual' THEN 'manual' ELSE doc_groups.source END",
            (doc_id, group_id, source))

    def _ensure_somewhere(self, doc_id: int) -> list[int]:
        """A document always belongs to a group: « À classer » when nothing else holds it; out of it otherwise."""
        conn = self.db.conn
        inbox = self.inbox_id()
        others = conn.execute("SELECT COUNT(*) FROM doc_groups WHERE doc_id = ? AND group_id != ?", (doc_id, inbox)).fetchone()[0]
        if others:
            conn.execute("DELETE FROM doc_groups WHERE doc_id = ? AND group_id = ?", (doc_id, inbox))
        else:
            self._assign(doc_id, inbox, "auto")
        return [inbox]

    def clear_auto(self, doc_id: int) -> list[int]:
        conn = self.db.conn
        gids = [r["group_id"] for r in conn.execute("SELECT group_id FROM doc_groups WHERE doc_id = ? AND source = 'auto'", (doc_id,))]
        conn.execute("DELETE FROM doc_groups WHERE doc_id = ? AND source = 'auto'", (doc_id,))
        return gids

    def _conflicts(self, kinds: list[str], doc_kv: dict[str, set[str]], group_kv: dict[str, set[str]],
                   amb: set[tuple[str, str]], strong_only: bool) -> bool:
        for kind in kinds:
            if strong_only and kind not in STRONG_KEYS:
                continue
            mine = doc_kv.get(kind) or set()
            theirs = {v for v in group_kv.get(kind, set()) if (kind, v) not in amb}
            if mine and theirs and not (mine & theirs):
                return True
        return False

    def _subgroup(self, parent: int, rule: TypeRule, doc_id: int, vec: np.ndarray | None) -> int:
        kinds = rule.subgroup_by
        if not kinds:
            return parent
        conn = self.db.conn
        blacklist = self.rules.blacklist
        doc_kv: dict[str, set[str]] = defaultdict(set)
        labels: dict[tuple[str, str], str] = {}
        for key, value, label in self.doc_keys(doc_id):
            if key in kinds and (key, fold(value)) not in blacklist:
                doc_kv[key].add(value)
                labels.setdefault((key, value), label)
        children = conn.execute("SELECT id, centroid FROM groups WHERE parent_id = ?", (parent,)).fetchall()
        child_ids = [c["id"] for c in children]
        child_keys = self._group_keys(child_ids)
        holders: dict[tuple[str, str], set[int]] = defaultdict(set)
        for cid, kv in child_keys.items():
            for kind, values in kv.items():
                for v in values:
                    holders[(kind, v)].add(cid)
        amb = {kv for kv, cids in holders.items() if len(cids) > 1}

        # 1. a shared key, weighted by the key's rank in subgroup_by
        scores: dict[int, float] = defaultdict(float)
        for rank, kind in enumerate(kinds):
            for value in doc_kv.get(kind, ()):
                if (kind, value) in amb:
                    continue
                for cid in holders.get((kind, value), ()):
                    scores[cid] += (len(kinds) - rank) * (2 if kind in STRONG_KEYS else 1)
        for cid in sorted(scores, key=lambda c: (-scores[c], c)):
            if not self._conflicts(kinds, doc_kv, child_keys[cid], amb, strong_only=True):
                return cid

        # 2. the closest centroid, if close enough and not contradicted by any key
        if vec is not None:
            best, best_sim = None, self.group_threshold
            for child in children:
                centroid = blob_to_vec(child["centroid"])
                if centroid is None:
                    continue
                sim = float(vec @ centroid)
                if sim >= best_sim and not self._conflicts(kinds, doc_kv, child_keys[child["id"]], amb, strong_only=False):
                    best, best_sim = child["id"], sim
            if best is not None:
                return best

        # 3. a new sub-group, named after the best key
        for kind in kinds:
            values = sorted(doc_kv.get(kind, ()))
            if values:
                # the first value found in the text gives the name
                ordered = [v for k, v, _l in self.doc_keys(doc_id) if k == kind and v in doc_kv[kind]]
                name = labels[(kind, ordered[0] if ordered else values[0])]
                cur = conn.execute("INSERT INTO groups(parent_id, name, auto, created_at) VALUES (?, ?, 1, ?)",
                                   (parent, name, now()))
                return cur.lastrowid
        return parent

    def _knn(self, doc_id: int, vec: np.ndarray) -> tuple[int | None, list[dict]]:
        conn = self.db.conn
        inbox = self.inbox_id()
        rows = conn.execute(
            "SELECT d.id, d.embedding FROM documents d WHERE d.id != ? AND d.status = 'done' AND d.embedding IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM doc_groups dg WHERE dg.doc_id = d.id AND dg.group_id != ?)", (doc_id, inbox)).fetchall()
        if not rows:
            return None, []
        ids = [r["id"] for r in rows]
        matrix = np.vstack([blob_to_vec(r["embedding"]) for r in rows])
        sims = matrix @ vec
        order = np.argsort(-sims)[:KNN_K]
        votes: dict[int, float] = defaultdict(float)
        counts: dict[int, int] = defaultdict(int)
        for i in order:
            groups = [r["group_id"] for r in conn.execute(
                "SELECT group_id FROM doc_groups WHERE doc_id = ? AND group_id != ?", (ids[i], inbox))]
            for gid in groups:
                votes[gid] += float(sims[i])
                counts[gid] += 1
        ranked = sorted(votes, key=lambda g: -votes[g])
        suggestions = [{"group_id": g, "score": round(votes[g] / max(counts[g], 1), 3), "votes": counts[g]} for g in ranked[:3]]
        if ranked:
            top = ranked[0]
            neighbours = len(order)
            if float(sims[order[0]]) >= self.knn_threshold and counts[top] * 2 > neighbours and neighbours >= 3:
                return top, suggestions
        return None, suggestions

    def place(self, doc_id: int) -> None:
        """Places a document whose types and keys are stored, its automatic assignments having been cleared."""
        conn = self.db.conn
        doc = conn.execute("SELECT id, types, locked, embedding FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            return
        touched: list[int] = []
        if doc["locked"]:
            touched += self._ensure_somewhere(doc_id)
            self.update_centroids(touched)
            return
        vec = blob_to_vec(doc["embedding"])
        types = json.loads(doc["types"] or "[]")
        suggestions: list[dict] = []
        applied = 0
        manual = conn.execute("SELECT COUNT(*) FROM doc_groups WHERE doc_id = ? AND source = 'manual'", (doc_id,)).fetchone()[0]
        placed = False
        for name in types:
            rule = self.rules.by_name(name)
            if rule is None:
                continue
            parent = self.type_group(rule)
            target = self._subgroup(parent, rule, doc_id, vec)
            self._assign(doc_id, target, "auto")
            if target != parent:
                self.learn_keys(target, doc_id, rule.subgroup_by)
            touched.append(target)
            placed = True
        if not placed and not manual and vec is not None:
            target, suggestions = self._knn(doc_id, vec)
            if target is not None:
                self._assign(doc_id, target, "auto")
                touched.append(target)
                applied = 1
                suggestions = []
        conn.execute("UPDATE documents SET suggestions = ?, suggestion_applied = ? WHERE id = ?",
                     (json.dumps(suggestions), applied, doc_id))
        touched += self._ensure_somewhere(doc_id)
        self.update_centroids(touched)

    def replace(self, doc_id: int) -> None:
        """Clears the automatic assignments of a document and places it again."""
        with self.db.tx():
            old = self.clear_auto(doc_id)
            self.place(doc_id)
            self.update_centroids(old)

    # ---------------------------------------------------------------- manual operations (learning from corrections)

    def add_manual(self, doc_id: int, group_id: int) -> None:
        with self.db.tx():
            self._assign(doc_id, group_id, "manual")
            rule = self.rule_of_group(group_id)
            parent = self.db.conn.execute("SELECT parent_id FROM groups WHERE id = ?", (group_id,)).fetchone()
            if parent and parent["parent_id"] is not None:
                kinds = rule.subgroup_by if rule else [k for k in
                                                       ("siret", "siren", "che_uid", "tva", "cadastre_ref", "property_address",
                                                        "contract_no", "employer_name")]
                self.learn_keys(group_id, doc_id, kinds, source="manual")
            self.db.conn.execute("UPDATE documents SET suggestions = '[]' WHERE id = ?", (doc_id,))
            self._ensure_somewhere(doc_id)
            self.update_centroids([group_id])

    def remove(self, doc_id: int, group_id: int) -> None:
        with self.db.tx():
            self.db.conn.execute("DELETE FROM doc_groups WHERE doc_id = ? AND group_id = ?", (doc_id, group_id))
            # what the document taught this group is forgotten, unless another member teaches it too
            self.db.conn.execute(
                "DELETE FROM group_keys WHERE group_id = ? AND source = 'auto' AND (key, value) IN "
                "(SELECT key, value FROM doc_keys WHERE doc_id = ?) AND NOT EXISTS (SELECT 1 FROM doc_groups dg "
                "JOIN doc_keys k ON k.doc_id = dg.doc_id WHERE dg.group_id = ? AND k.key = group_keys.key "
                "AND k.value = group_keys.value)", (group_id, doc_id, group_id))
            self._ensure_somewhere(doc_id)
            self.update_centroids([group_id])

    def move(self, doc_id: int, from_group: int | None, to_group: int) -> None:
        if from_group is not None and from_group != to_group:
            with self.db.tx():
                self.db.conn.execute("DELETE FROM doc_groups WHERE doc_id = ? AND group_id = ?", (doc_id, from_group))
                self.update_centroids([from_group])
        self.add_manual(doc_id, to_group)

    # ---------------------------------------------------------------- group operations

    def create(self, name: str, parent_id: int | None, color: str | None = None) -> int:
        with self.db.tx():
            if parent_id is None and not color:
                color = self._next_color()
            cur = self.db.conn.execute("INSERT INTO groups(parent_id, name, color, created_at) VALUES (?, ?, ?, ?)",
                                       (parent_id, name, color, now()))
            return cur.lastrowid

    def reparent(self, group_id: int, parent_id: int | None) -> None:
        if parent_id is not None and parent_id in self.descendants(group_id):
            raise ValueError("Un groupe ne peut pas être déplacé dans lui-même ou dans un de ses sous-groupes.")
        with self.db.tx():
            self.db.conn.execute("UPDATE groups SET parent_id = ? WHERE id = ?", (parent_id, group_id))

    def merge(self, source: int, target: int) -> None:
        if source == target:
            return
        if target in self.descendants(source):
            raise ValueError("Impossible de fusionner un groupe dans un de ses sous-groupes.")
        conn = self.db.conn
        with self.db.tx():
            conn.execute("INSERT INTO doc_groups(doc_id, group_id, source) SELECT doc_id, ?, source FROM doc_groups "
                         "WHERE group_id = ? ON CONFLICT(doc_id, group_id) DO UPDATE SET source = CASE WHEN "
                         "excluded.source = 'manual' THEN 'manual' ELSE doc_groups.source END", (target, source))
            conn.execute("INSERT OR IGNORE INTO group_keys(group_id, key, value, label, source) "
                         "SELECT ?, key, value, label, source FROM group_keys WHERE group_id = ?", (target, source))
            conn.execute("UPDATE groups SET parent_id = ? WHERE parent_id = ?", (target, source))
            conn.execute("DELETE FROM groups WHERE id = ?", (source,))
            # a merged group stays put: the automatic placement must not split it again
            conn.execute("UPDATE groups SET auto = 0 WHERE id = ?", (target,))
            self.update_centroids([target])

    def delete(self, group_id: int) -> None:
        """Documents go up to the parent (« À classer » for a top-level group); sub-groups go up too."""
        conn = self.db.conn
        with self.db.tx():
            row = conn.execute("SELECT parent_id, is_inbox FROM groups WHERE id = ?", (group_id,)).fetchone()
            if row is None:
                return
            if row["is_inbox"]:
                raise ValueError("Le groupe « À classer » ne peut pas être supprimé.")
            parent = row["parent_id"]
            docs = [r["doc_id"] for r in conn.execute("SELECT doc_id FROM doc_groups WHERE group_id = ?", (group_id,))]
            conn.execute("UPDATE groups SET parent_id = ? WHERE parent_id = ?", (parent, group_id))
            if parent is not None:
                conn.execute("INSERT OR IGNORE INTO doc_groups(doc_id, group_id, source) SELECT doc_id, ?, source "
                             "FROM doc_groups WHERE group_id = ?", (parent, group_id))
            conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
            for doc_id in docs:
                self._ensure_somewhere(doc_id)
            self.update_centroids([parent] if parent else [])

    def prune_empty(self) -> None:
        """Automatically created groups left without documents or sub-groups disappear."""
        conn = self.db.conn
        while True:
            cur = conn.execute("DELETE FROM groups WHERE auto = 1 AND is_inbox = 0 AND id NOT IN "
                               "(SELECT group_id FROM doc_groups) AND id NOT IN (SELECT parent_id FROM groups "
                               "WHERE parent_id IS NOT NULL)")
            if cur.rowcount == 0:
                break

    def reclassify(self, group_id: int | None, retype) -> int:
        """Runs the placement again on the automatic assignments (of one group's subtree, or all).
        `retype(doc_id)` recomputes a document's types from its text with the current rules."""
        conn = self.db.conn
        with self.db.tx():
            if group_id is None:
                docs = [r["id"] for r in conn.execute("SELECT id FROM documents WHERE status = 'done' AND locked = 0")]
                conn.execute("DELETE FROM group_keys WHERE source = 'auto'")
            else:
                subtree = self.descendants(group_id)
                marks = ",".join("?" * len(subtree))
                docs = [r["doc_id"] for r in conn.execute(
                    f"SELECT DISTINCT dg.doc_id FROM doc_groups dg JOIN documents d ON d.id = dg.doc_id WHERE "
                    f"dg.group_id IN ({marks}) AND dg.source = 'auto' AND d.locked = 0", subtree)]
                conn.execute(f"DELETE FROM group_keys WHERE source = 'auto' AND group_id IN ({marks})", subtree)
            touched = set()
            for doc_id in docs:
                touched.update(self.clear_auto(doc_id))
            # members placed by hand teach their groups again first
            for r in conn.execute("SELECT doc_id, group_id FROM doc_groups WHERE source = 'manual'").fetchall():
                rule = self.rule_of_group(r["group_id"])
                self.learn_keys(r["group_id"], r["doc_id"], rule.subgroup_by if rule else None, source="manual")
            for doc_id in docs:
                retype(doc_id)
                self.place(doc_id)
            self.update_centroids(touched)
            self.prune_empty()
        return len(docs)
