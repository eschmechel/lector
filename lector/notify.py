import asyncio

APP = "lector"


async def notify(summary: str, body: str = "", urgency: str = "normal") -> None:
    proc = await asyncio.create_subprocess_exec(
        "dunstify", "-a", APP, "-u", urgency, summary, body,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def ask(summary: str, body: str, actions: list[tuple[str, str]],
              timeout_ms: int = 60000) -> str | None:
    """Show an actionable notification; return the chosen action key or None."""
    args = ["dunstify", "-a", APP, "-t", str(timeout_ms)]
    for key, label in actions:
        args += ["-A", f"{key},{label}"]
    args += [summary, body]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    choice = out.decode().strip()
    return choice if choice in {k for k, _ in actions} else None
