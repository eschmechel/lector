import os
import re

import httpx


class BrainUnavailable(RuntimeError):
    pass


SUMMARIZE_SYSTEM = """\
You are a note-taker. Produce a structured markdown summary of the document:

## TL;DR
2-3 sentences.

## Key points
Tight bullets, the substance only.

## Notable quotes
Up to 3 short verbatim quotes worth keeping (omit section if none).

## Open questions
What the document leaves unanswered (omit section if none).

No preamble, no meta-commentary. Output markdown only."""

ANNOTATE_SYSTEM = """\
You are an annotator. Return the document's own text interleaved with margin notes:
reproduce each section (trim boilerplate), and after each meaningful passage add a
blockquote note like:

> **note:** your observation — significance, connections, caveats, or disagreements.

Keep the document's structure and headings. Notes should be sharp and occasional
(a few per section), not a running commentary. Output markdown only, no preamble."""

SMARTREAD_SYSTEM = """\
Rewrite the input as clear spoken narration for text-to-speech listening.
Rules: expand CLI flags and symbols into words ("-h" -> "the h flag", "--help" ->
"the help flag", "~/" -> "home directory"); describe tables and code blocks briefly
in prose instead of reading them cell by cell; spell out abbreviations on first use
when helpful; keep ALL substantive information; add nothing that isn't in the input.
Output plain prose only — no markdown, no lists, no preamble."""

_PROMPTS = {"summarize": SUMMARIZE_SYSTEM, "annotate": ANNOTATE_SYSTEM,
            "smart": SMARTREAD_SYSTEM}
_WORD_CAPS = {"summarize": 8000, "annotate": 4000, "smart": 2500}

_THINK_RE = re.compile(r"<think>.*?(</think>|\Z)", re.S)


def strip_thinking(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


class Brain:
    def __init__(self, cfg):
        self.cfg = cfg

    def _cloud_conf(self) -> tuple[str, str, str]:
        base = os.environ.get("LECTOR_CLOUD_BASE_URL") or self.cfg.llm_cloud_base_url
        model = os.environ.get("LECTOR_CLOUD_MODEL") or self.cfg.llm_cloud_model
        key = os.environ.get("LECTOR_CLOUD_API_KEY", "")
        if not base or not model:
            raise BrainUnavailable(
                "cloud lane not configured (LECTOR_CLOUD_BASE_URL / _MODEL / _API_KEY)")
        return base.rstrip("/"), model, key

    def complete(self, system: str, user: str, cloud: bool = False) -> str:
        """Blocking chat completion — call from a thread executor."""
        try:
            if cloud or self.cfg.llm_provider == "cloud":
                base, model, key = self._cloud_conf()
                r = httpx.post(
                    f"{base}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"} if key else {},
                    json={"model": model, "stream": False,
                          "messages": [{"role": "system", "content": system},
                                       {"role": "user", "content": user}]},
                    timeout=120,
                )
                r.raise_for_status()
                out = r.json()["choices"][0]["message"]["content"]
            else:
                base = self.cfg.llm_local_base_url.rstrip("/")
                r = httpx.post(
                    f"{base}/api/chat",
                    json={"model": self.cfg.llm_local_model, "stream": False,
                          "think": False,
                          "options": {"temperature": 0.3, "num_ctx": 16384},
                          "messages": [{"role": "system", "content": system},
                                       {"role": "user", "content": user}]},
                    timeout=300,
                )
                r.raise_for_status()
                out = r.json()["message"]["content"]
        except httpx.ConnectError as e:
            raise BrainUnavailable(
                f"LLM endpoint unreachable ({e}) — is ollama running?") from e
        except httpx.HTTPStatusError as e:
            raise BrainUnavailable(
                f"LLM returned {e.response.status_code}: {e.response.text[:200]}") from e
        return strip_thinking(out)

    def run(self, mode: str, title: str, text: str, cloud: bool = False) -> str:
        cap = _WORD_CAPS[mode]
        words = text.split()
        truncated = ""
        if len(words) > cap:
            text = " ".join(words[:cap])
            truncated = f"\n\n(Input truncated to the first {cap} words.)"
        user = f"Document title: {title}\n\n{text}"
        return self.complete(_PROMPTS[mode], user, cloud=cloud) + truncated
