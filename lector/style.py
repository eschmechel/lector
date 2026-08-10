"""Personalization: style card, per-app tone profiles, phrase shortcuts, and the
dictionary learned from your corrections.

Everything here is a file you can open and read. That is deliberate — a prompt you
can inspect beats a fine-tune you cannot, especially on a corpus this small.
"""

import difflib
import json
import re
from pathlib import Path

MAX_EXAMPLES = 25
MAX_PHRASE_WORDS = 4


class StyleBook:
    def __init__(self, cfg):
        self.cfg = cfg
        self.card_path: Path = cfg.style_card
        self.learned_path: Path = cfg.style_card.parent / "learned.json"
        self._card = ""
        self._learned: dict = {"corrections": {}, "examples": []}
        self.reload()

    # ------------------------------------------------------------------ loading

    def reload(self) -> None:
        try:
            self._card = self.card_path.read_text().strip()
        except OSError:
            self._card = ""
        try:
            data = json.loads(self.learned_path.read_text())
            self._learned = {"corrections": dict(data.get("corrections", {})),
                             "examples": list(data.get("examples", []))}
        except (OSError, json.JSONDecodeError, TypeError):
            self._learned = {"corrections": {}, "examples": []}

    def _save(self) -> None:
        self.learned_path.parent.mkdir(parents=True, exist_ok=True)
        self.learned_path.write_text(json.dumps(self._learned, indent=2,
                                                ensure_ascii=False) + "\n")

    @property
    def corrections(self) -> dict[str, str]:
        return self._learned["corrections"]

    # ------------------------------------------------------------------ text fixes

    @staticmethod
    def _replace_phrases(text: str, mapping: dict[str, str]) -> str:
        if not text or not mapping:
            return text
        # Longest first so "my calendar link" wins over "my calendar".
        for phrase in sorted(mapping, key=len, reverse=True):
            pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)
            text = pattern.sub(mapping[phrase].replace("\\", r"\\"), text)
        return text

    def expand_shortcuts(self, text: str) -> str:
        return self._replace_phrases(text, self.cfg.shortcuts)

    def apply_corrections(self, text: str) -> str:
        """Fix mis-transcriptions learned from previous corrections. Applies to the
        raw tier too, so proper nouns survive without invoking the LLM."""
        return self._replace_phrases(text, self.corrections)

    # ------------------------------------------------------------------ prompting

    def profile_for(self, window_class: str) -> str:
        if not window_class:
            return ""
        profiles = self.cfg.style_profiles
        if window_class in profiles:
            return profiles[window_class]
        low = window_class.lower()
        for known, text in profiles.items():
            if known.lower() in low:
                return text
        return ""

    def vocabulary(self) -> list[str]:
        seen = list(self.cfg.vocabulary)
        seen += [v for v in self.corrections.values() if v not in seen]
        return seen

    def system_prompt(self, base: str, window_class: str = "") -> str:
        parts = [base]
        if self._card:
            parts.append("The user's writing style, in their own words:\n" + self._card)
        profile = self.profile_for(window_class)
        if profile:
            parts.append(f"Target application tone: {profile}")
        vocab = self.vocabulary()
        if vocab:
            parts.append("Spell these terms exactly when they occur: "
                         + ", ".join(sorted(set(vocab))))
        examples = self._learned["examples"][-3:]
        if examples:
            shown = "\n\n".join(f"Instead of: {e['before']}\nThe user wrote: {e['after']}"
                                for e in examples)
            parts.append("Past corrections from this user:\n" + shown)
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ learning

    def learn(self, before: str, after: str) -> dict:
        """Mine a (what lector produced, what the user fixed it to) pair.

        Short word-level substitutions become dictionary entries; the whole pair is
        kept as a style example. Returns a summary of what changed.
        """
        before, after = before.strip(), after.strip()
        if not before or not after or before == after:
            return {"corrections": 0, "example": False}

        b_words, a_words = before.split(), after.split()
        added = 0
        matcher = difflib.SequenceMatcher(a=b_words, b=a_words, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "replace":
                continue
            src = " ".join(b_words[i1:i2])
            dst = " ".join(a_words[j1:j2])
            if not src or not dst:
                continue
            if (i2 - i1) > MAX_PHRASE_WORDS or (j2 - j1) > MAX_PHRASE_WORDS:
                continue  # a rewrite, not a vocabulary fix
            if src.lower() == dst.lower():
                continue
            self.corrections[src] = dst
            added += 1

        self._learned["examples"].append({"before": before, "after": after})
        self._learned["examples"] = self._learned["examples"][-MAX_EXAMPLES:]
        self._save()
        return {"corrections": added, "example": True}
