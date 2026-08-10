import asyncio

APP = "lector"


async def notify(summary: str, body: str = "", urgency: str = "normal",
                 timeout_ms: int | None = None) -> None:
    args = ["dunstify", "-a", APP, "-u", urgency]
    if timeout_ms is not None:
        args += ["-t", str(timeout_ms)]
    proc = await asyncio.create_subprocess_exec(
        *args, summary, body,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


MIC_NOTIFY_ID = 9911


async def progress(summary: str, body: str, value: int,
                   replace_id: int = MIC_NOTIFY_ID) -> None:
    """A notification that replaces itself, with dunst's progress bar as a level
    meter. Cheaper and less intrusive than an always-on-top overlay window."""
    proc = await asyncio.create_subprocess_exec(
        "dunstify", "-a", APP, "-r", str(replace_id), "-t", "1500",
        "-h", f"int:value:{max(0, min(100, value))}", summary, body,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()


async def close(replace_id: int = MIC_NOTIFY_ID) -> None:
    proc = await asyncio.create_subprocess_exec(
        "dunstify", "-C", str(replace_id),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()


async def ask(summary: str, body: str, actions: list[tuple[str, str]],
              timeout_ms: int = 60000) -> str | None:
    """Actionable dunst notification (actions fire on middle-click in most configs).

    Only suitable for a single optional action — for real choices use Daemon.ui_ask,
    which opens the floating fzf chooser."""
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
