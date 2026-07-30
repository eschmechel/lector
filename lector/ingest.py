import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Section:
    title: str
    text: str


@dataclass
class Doc:
    title: str
    sections: list[Section] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return sum(len(s.text.split()) for s in self.sections)


def clean_markdown(text: str) -> str:
    text = re.sub(r"```.*?(```|\Z)", "\n[code block omitted]\n", text, flags=re.S)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>\n]+>", "", text)

    lines, out, in_table = text.splitlines(), [], False
    for line in lines:
        if re.match(r"^\s*\|.*\|\s*$", line):
            if not in_table:
                out.append("[table omitted]")
                in_table = True
            continue
        in_table = False
        # bullets read better as plain sentences
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1", line)
        line = re.sub(r"^(\s*)>\s?", r"\1", line)
        out.append(line)
    text = "\n".join(out)

    text = re.sub(r"(\*\*|__|~~)", "", text)
    text = re.sub(r"(?<![\w])[*_](\S[^*_\n]*?)[*_](?![\w])", r"\1", text)
    text = re.sub(r"^\s*([-=*]){3,}\s*$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_sections(text: str, fallback_title: str) -> list[Section]:
    """Split on h1/h2 markdown headings; h3+ stays inline within its section."""
    parts = re.split(r"^(#{1,2})\s+(.+)$", text, flags=re.M)
    sections: list[Section] = []
    intro = parts[0].strip()
    if intro:
        sections.append(Section(fallback_title, clean_markdown(intro)))
    for i in range(1, len(parts), 3):
        title = parts[i + 1].strip()
        body = clean_markdown(re.sub(r"^#{3,}\s+", "", parts[i + 2], flags=re.M).strip())
        if body or title:
            sections.append(Section(title, body))
    if not sections:
        sections = [Section(fallback_title, clean_markdown(text))]
    return [s for s in sections if s.text or len(sections) == 1]


def load_pdf(path: Path) -> Doc:
    import pymupdf

    pdf = pymupdf.open(path)
    try:
        title = (pdf.metadata or {}).get("title") or path.stem
        pages = [page.get_text("text") for page in pdf]
        toc = [(t, pno) for level, t, pno in pdf.get_toc() if level == 1]
        sections: list[Section] = []
        if len(toc) >= 2:
            bounds = [max(0, pno - 1) for _, pno in toc] + [len(pages)]
            if bounds[0] > 0:
                sections.append(Section(title, "\n".join(pages[: bounds[0]])))
            for i, (t, _) in enumerate(toc):
                body = "\n".join(pages[bounds[i]: bounds[i + 1]])
                sections.append(Section(t, body.strip()))
        else:
            sections = [Section(f"Page {i + 1}", p.strip()) for i, p in enumerate(pages)]
        sections = [s for s in sections if s.text]
        return Doc(title, sections or [Section(title, "")])
    finally:
        pdf.close()


def load_source(value: str | Path) -> Doc:
    if isinstance(value, Path):
        if value.suffix.lower() == ".pdf":
            return load_pdf(value)
        text = value.read_text(errors="replace")
        title = value.stem.replace("-", " ").replace("_", " ")
        m = re.match(r"^#\s+(.+)$", text.lstrip().splitlines()[0] if text.strip() else "")
        if m:
            title = m.group(1).strip()
        return Doc(title, split_sections(text, title))
    text = str(value)
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "clipboard")
    title = re.sub(r"^#+\s*", "", first_line)[:60]
    return Doc(title, split_sections(text, title))
