"""Signal object for Ctrl+B background escape in REPL mode.

When the user presses Ctrl+B during an active agent run in the REPL,
the LiveStatus keybinding handler invokes the ``on_background`` callback,
which sets a flag that causes ``chat()`` to raise this exception.
``chat()`` then catches it and triggers the background runner fork.

Using an exception (rather than a callback that directly calls ``os.fork``)
keeps the LiveStatus keybinding handler free of process-management logic —
it only signals intent, ``chat()`` decides what to do about it.
"""


class BackgroundEscape(Exception):
    """Raised when the user presses Ctrl+B during an active agent run.

    The REPL's ``chat()`` method catches this exception to trigger
    the background runner fork.  Using an exception (rather than a
    callback that directly calls ``os.fork``) keeps the LiveStatus
    keybinding handler free of process-management logic — it only
    signals intent, ``chat()`` decides what to do about it.
    """

    def __init__(self, message: str = "Background escape requested") -> None:
        super().__init__(message)
