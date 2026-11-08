"""The classification rules (rules.yaml): types with weighted keywords, the keys that name their sub-groups,
the tags they add. The default file ships with the image; conf/rules.yaml replaces it when present."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .identifiers import KEY_NAMES
from .text import fold

DEFAULT_PATH = Path(__file__).resolve().parent / "default_rules.yaml"


class RulesError(Exception):
    pass


@dataclass
class TypeRule:
    name: str
    keywords: dict[str, float]
    threshold: float
    subgroup_by: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    skip_if: list[str] = field(default_factory=list)
    color: str | None = None
    patterns: list[tuple[re.Pattern, float, str]] = field(default_factory=list)

    def score(self, folded: str) -> tuple[float, list[str]]:
        total, hits = 0.0, []
        for pattern, weight, word in self.patterns:
            if pattern.search(folded):
                total += weight
                hits.append(word)
        return total, hits


@dataclass
class Rules:
    types: list[TypeRule]
    blacklist: set[tuple[str, str]]
    source: str
    # tag -> patterns: a tag given to any document whose text holds one of its words, whatever its type
    tag_rules: list = field(default_factory=list)

    def content_tags(self, text: str, title: str = "") -> list[str]:
        """Tags from the words of the document: a word counts when it is in the title or the first 4000 characters,
        or comes back at least twice in the text (one stray mention deep in a long text is not a subject)."""
        head = fold(title + "\n" + text[:4000])
        full = fold(text[:100_000])
        out = []
        for tag, patterns in self.tag_rules:
            for pattern in patterns:
                if pattern.search(head) or len(pattern.findall(full)) >= 2:
                    out.append(tag)
                    break
        return out

    def by_name(self, name: str) -> TypeRule | None:
        for t in self.types:
            if t.name == name:
                return t
        return None

    def match(self, text: str, title: str = "") -> list[tuple[TypeRule, float]]:
        """The types a document belongs to, best first."""
        folded = fold(title.replace("_", " ").replace("-", " ") + "\n" + text[:100_000])
        matched = []
        for rule in self.types:
            score, _hits = rule.score(folded)
            if score >= rule.threshold:
                matched.append((rule, score))
        names = {r.name for r, _ in matched}
        matched = [(r, s) for r, s in matched if not any(skip in names for skip in r.skip_if)]
        return sorted(matched, key=lambda rs: -rs[1])


def _pattern(word: str) -> re.Pattern:
    folded = fold(word)
    stripped = folded.strip()
    body = re.escape(stripped).replace(r"\ ", r"\s+")
    # a keyword written with a trailing space ("dr ", "ac ") must end there; others end at a word boundary
    left = r"(?<![a-z0-9])"
    right = r"(?![a-z0-9])" if folded == stripped else r"(?=\s)"
    if not stripped[-1:].isalnum():
        right = ""
    return re.compile(left + body + right)


def parse(text: str, source: str) -> Rules:
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as error:
        raise RulesError(f"{source}: YAML invalide ({error})") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("types"), list):
        raise RulesError(f"{source}: une liste « types: » est attendue")
    types = []
    for i, item in enumerate(raw["types"], 1):
        if not isinstance(item, dict) or not item.get("name"):
            raise RulesError(f"{source}: le type n°{i} n'a pas de nom")
        keywords = item.get("keywords") or {}
        if not isinstance(keywords, dict) or not keywords:
            raise RulesError(f"{source}: « {item['name']} » n'a pas de mots-clés")
        subgroup_by = list(item.get("subgroup_by") or [])
        unknown = [k for k in subgroup_by if k not in KEY_NAMES]
        if unknown:
            raise RulesError(f"{source}: « {item['name']} » : clé inconnue {unknown} (connues : {', '.join(KEY_NAMES)})")
        rule = TypeRule(
            name=str(item["name"]),
            keywords={str(k): float(v) for k, v in keywords.items()},
            threshold=float(item.get("threshold", 4)),
            subgroup_by=subgroup_by,
            tags=[str(t) for t in item.get("tags") or []],
            skip_if=[str(t) for t in item.get("skip_if") or []],
            color=item.get("color"),
        )
        rule.patterns = [(_pattern(word), weight, word) for word, weight in rule.keywords.items() if word.strip()]
        types.append(rule)
    blacklist = set()
    for entry in raw.get("blacklist") or []:
        if isinstance(entry, dict) and entry.get("key") and entry.get("value"):
            blacklist.add((str(entry["key"]), fold(str(entry["value"])).strip()))
    tag_rules = []
    raw_tags = raw.get("tag_keywords")
    if raw_tags is not None and not isinstance(raw_tags, dict):
        raise RulesError(f"{source}: « tag_keywords: » doit associer un tag à une liste de mots")
    for tag, words in (raw_tags or {}).items():
        if isinstance(words, str):
            words = [words]
        tag_rules.append((str(tag), [_pattern(str(w)) for w in words if str(w).strip()]))
    return Rules(types=types, blacklist=blacklist, source=source, tag_rules=tag_rules,
                 )


class RulesStore:
    """The rules in use; reload() swaps them atomically (a bad file keeps the previous rules)."""

    def __init__(self, custom: Path):
        self.custom = custom
        self._lock = threading.Lock()
        self._rules = self._load()

    def _load(self) -> Rules:
        default = parse(DEFAULT_PATH.read_text(encoding="utf-8"), "règles par défaut")
        if not self.custom.is_file():
            return default
        custom = parse(self.custom.read_text(encoding="utf-8"), str(self.custom))
        if "tag_keywords" not in (yaml.safe_load(self.custom.read_text(encoding="utf-8")) or {}):
            custom.tag_rules = default.tag_rules  # an older custom file keeps the shipped content tags
        return custom

    @property
    def rules(self) -> Rules:
        with self._lock:
            return self._rules

    def reload(self) -> Rules:
        rules = self._load()
        with self._lock:
            self._rules = rules
        return rules
