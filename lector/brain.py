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

CLEAN_SYSTEM = """\
You are a dictation post-processor. The input is a raw speech-to-text transcript.

Fix punctuation, capitalization, and obvious mis-transcriptions. Remove filler words
(um, uh, er, like, you know) and false starts, keeping the user's actual wording
otherwise. Honour spoken formatting instructions — "new line", "new paragraph",
"bullet point", "quote" — by applying the formatting rather than transcribing the
words. If the user audibly corrects themselves ("...the red one, sorry, the blue
one"), keep only the correction.

Never answer, continue, summarize, or comment on the text. Never add information.
Output only the cleaned text."""

SCRIBE_WRITE_SYSTEM = """\
The user spoke a rough description of what they want to say. Write the finished text
for them, in their voice, ready to send or paste as-is.

Match the length implied by the intent — a one-line reply stays one line. Do not add
greetings, sign-offs, or subject lines unless the intent calls for them. Do not
explain what you wrote or offer alternatives.

Output only the finished text."""

SCRIBE_REWRITE_SYSTEM = """\
The user selected a passage and spoke an instruction about it. Apply the instruction
to the passage and return the result, which will replace the selection directly.

Preserve the passage's format (markdown stays markdown, code stays code) unless the
instruction says otherwise. Change only what the instruction asks for. Do not explain
the change, do not wrap the result in quotes or code fences that were not there.

Output only the rewritten passage."""

_PROMPTS = {"summarize": SUMMARIZE_SYSTEM, "annotate": ANNOTATE_SYSTEM,
            "smart": SMARTREAD_SYSTEM, "clean": CLEAN_SYSTEM,
            "scribe": SCRIBE_WRITE_SYSTEM, "scribe_rewrite": SCRIBE_REWRITE_SYSTEM}
_WORD_CAPS = {"summarize": 8000, "annotate": 4000, "smart": 2500,
              "clean": 3000, "scribe": 3000, "scribe_rewrite": 3000}

_THINK_RE = re.compile(r"<think>.*?(</think>|\Z)", re.S)


def strip_thinking(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


class Brain:
    def __init__(self, cfg):
        self.cfg = cfg
        # Which lane the last call actually used — the daemon serializes foreground
        # jobs, so this is safe to read right after a call returns.
        self.last_lane = "local"
        self.last_fallback_reason = ""

    def _cloud_conf(self) -> tuple[str, str, str]:
        base = os.environ.get("LECTOR_CLOUD_BASE_URL") or self.cfg.llm_cloud_base_url
        model = os.environ.get("LECTOR_CLOUD_MODEL") or self.cfg.llm_cloud_model
        key = os.environ.get("LECTOR_CLOUD_API_KEY", "")
        if not base or not model:
            raise BrainUnavailable(
                "cloud lane not configured (LECTOR_CLOUD_BASE_URL / _MODEL)")
        return base.rstrip("/"), model, key

    def _cloud(self, system: str, user: str) -> str:
        base, model, key = self._cloud_conf()
        try:
            r = httpx.post(
                f"{base}/chat/completions",
                # Aperture authorizes by tailnet identity; the header is only sent
                # when a key is actually configured.
                headers={"Authorization": f"Bearer {key}"} if key else {},
                json={"model": model, "stream": False,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
                timeout=120,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            raise BrainUnavailable(
                f"cloud LLM returned {e.response.status_code}: "
                f"{e.response.text[:200]}") from e
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise BrainUnavailable(f"cloud LLM unreachable ({e})") from e
        except (KeyError, IndexError, ValueError) as e:
            raise BrainUnavailable(f"cloud LLM sent an unusable reply: {e}") from e

    def _local(self, system: str, user: str) -> str:
        base = self.cfg.llm_local_base_url.rstrip("/")
        try:
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
            return r.json()["message"]["content"]
        except httpx.ConnectError as e:
            raise BrainUnavailable(
                f"local LLM unreachable ({e}) — is ollama running?") from e
        except httpx.HTTPStatusError as e:
            raise BrainUnavailable(
                f"local LLM returned {e.response.status_code}: "
                f"{e.response.text[:200]}") from e
        except (KeyError, ValueError) as e:
            raise BrainUnavailable(f"local LLM sent an unusable reply: {e}") from e

    def complete(self, system: str, user: str, cloud: bool = False,
                 lane: str | None = None) -> str:
        """Blocking chat completion — call from a thread executor.

        A cloud lane that fails falls back to local unless disabled, so an
        unreachable tailnet degrades quality instead of erroring.
        """
        if lane is None:
            lane = "cloud" if (cloud or self.cfg.llm_provider == "cloud") else "local"
        self.last_fallback_reason = ""
        if lane == "cloud":
            try:
                self.last_lane = "cloud"
                return strip_thinking(self._cloud(system, user))
            except BrainUnavailable as e:
                if not self.cfg.llm_cloud_fallback_local:
                    raise
                self.last_fallback_reason = str(e)
        self.last_lane = "local"
        return strip_thinking(self._local(system, user))

    def _cap(self, mode: str, text: str) -> tuple[str, str]:
        cap = _WORD_CAPS[mode]
        words = text.split()
        if len(words) <= cap:
            return text, ""
        return " ".join(words[:cap]), f"\n\n(Input truncated to the first {cap} words.)"

    def run(self, mode: str, title: str, text: str, cloud: bool = False,
            system: str | None = None) -> str:
        text, truncated = self._cap(mode, text)
        lane = "cloud" if cloud else self.cfg.lane_for(mode)
        user = f"Document title: {title}\n\n{text}"
        return self.complete(system or _PROMPTS[mode], user, lane=lane) + truncated

    def run_voice(self, mode: str, text: str, instruction: str = "",
                  cloud: bool = False, system: str | None = None) -> str:
        """Dictation-side modes: no document title, optional spoken instruction."""
        text, _ = self._cap(mode, text)
        lane = "cloud" if cloud else self.cfg.lane_for(mode)
        if instruction:
            user = f"Instruction: {instruction}\n\nPassage:\n{text}"
        else:
            user = text
        return self.complete(system or _PROMPTS[mode], user, lane=lane)
