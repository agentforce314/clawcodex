"""Tests for ``src/tui/vim_persistent.py`` — ``PersistentState`` +
``RecordedChange`` + ``replay()`` (dot-repeat machinery).

Phase 2 of the ch14 refactor. Verifies that each ``RecordedChange``
variant replays correctly via ``replay()`` against a fake context,
that ``PersistentState`` accumulates state across commands, and that
the integration of dot-repeat (`.`) and find-repeat (`;`/`,`) through
the state machine produces correct results.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.tui.vim_buffer import Cursor
from src.tui.vim_persistent import (
    IndentChange,
    InsertChange,
    JoinChange,
    OpenLineChange,
    OperatorChange,
    OperatorFindChange,
    OperatorTextObjChange,
    PersistentState,
    RecordedChange,
    ReplaceChange,
    ToggleCaseChange,
    XChange,
    replay,
)
from src.tui.vim_state import (
    FindState,
    IdleState,
    OperatorState,
    TransitionContext,
    transition,
)


@dataclass
class _FakeCtx:
    """Shared fake ctx — identical to test_vim_transitions.py for parity."""

    text: str = ""
    cursor: Cursor = field(default_factory=lambda: Cursor(0, 0))
    register_content: str = ""
    register_linewise: bool = False
    last_find: tuple[str, str] | None = None
    last_change: RecordedChange | None = None
    changes_recorded: list[RecordedChange] = field(default_factory=list)

    def as_transition_context(self) -> TransitionContext:
        return TransitionContext(
            get_cursor=lambda: self.cursor,
            set_cursor=self._set_cursor,
            get_text=lambda: self.text,
            set_text=self._set_text,
            set_register=self._set_register,
            get_register=lambda: (self.register_content, self.register_linewise),
            get_last_find=lambda: self.last_find,  # type: ignore[return-value]
            set_last_find=self._set_last_find,
            get_last_change=lambda: self.last_change,
            record_change=self._record_change,
        )

    def _set_cursor(self, cursor: Cursor) -> None:
        self.cursor = cursor

    def _set_text(self, text: str) -> None:
        self.text = text

    def _set_register(self, content: str, linewise: bool) -> None:
        self.register_content = content
        self.register_linewise = linewise

    def _set_last_find(self, find_type, char):  # type: ignore[no-untyped-def]
        self.last_find = (find_type, char)

    def _record_change(self, change: RecordedChange) -> None:
        self.changes_recorded.append(change)
        self.last_change = change


# ---- PersistentState basics -----------------------------------------------


def test_persistent_state_defaults():
    p = PersistentState()
    assert p.last_change is None
    assert p.last_find is None
    assert p.register == ""
    assert p.register_is_linewise is False


def test_persistent_state_record_change():
    p = PersistentState()
    p.record(OperatorChange(op="delete", motion="w", count=1))
    assert p.last_change == OperatorChange(op="delete", motion="w", count=1)


def test_persistent_state_record_find():
    p = PersistentState()
    p.record_find("f", "a")
    assert p.last_find == ("f", "a")


def test_persistent_state_set_register_linewise():
    p = PersistentState()
    p.set_register("line one\n", linewise=True)
    assert p.register == "line one\n"
    assert p.register_is_linewise is True


# ---- replay() per-variant -------------------------------------------------


def test_replay_operator_change_dw():
    """After `dw`, dot-replay deletes another word at the current cursor."""

    ctx = _FakeCtx(text="hello world there", cursor=Cursor(0, 0))
    change = OperatorChange(op="delete", motion="w", count=1)
    replay(change, ctx.as_transition_context())
    assert ctx.text == "world there"


def test_replay_operator_change_dd_clears_line():
    """`dd` on a single-line buffer clears it."""

    ctx = _FakeCtx(text="hello", cursor=Cursor(0, 0))
    change = OperatorChange(op="delete", motion="d", count=1)
    replay(change, ctx.as_transition_context())
    assert ctx.text == ""
    assert ctx.register_linewise is True


def test_replay_operator_text_obj_change():
    """ciw on word boundary."""

    ctx = _FakeCtx(text="hello world", cursor=Cursor(0, 6))
    change = OperatorTextObjChange(
        op="change", obj_type="w", scope="inner", count=1
    )
    replay(change, ctx.as_transition_context())
    assert ctx.text == "hello "


def test_replay_operator_find_change_df_inclusive():
    """`df"` deletes from cursor through the quote (inclusive)."""

    ctx = _FakeCtx(text='hello"world', cursor=Cursor(0, 0))
    change = OperatorFindChange(op="delete", find="f", char='"', count=1)
    replay(change, ctx.as_transition_context())
    assert ctx.text == "world"


def test_replay_operator_find_change_dF_inclusive():
    """`dF"` from after the quote deletes through cursor inclusive."""

    ctx = _FakeCtx(text='abc"def', cursor=Cursor(0, 6))  # cursor on 'f'
    change = OperatorFindChange(op="delete", find="F", char='"', count=1)
    replay(change, ctx.as_transition_context())
    # Deletes '"def' — keeps 'abc'.
    assert ctx.text == "abc"


def test_replay_operator_find_change_dT_exclusive_with_cursor():
    """`dT<h>` from `hello world` cursor on 'o' (col 7).

    Vim/TS semantics: ``dT`` deletes ``[motion-target, cursor]`` inclusive.
    ``find_char`` for T returns the motion target (one-after the matched
    char) directly; operator-find treats it as the endpoint without
    further adjustment (TS ``vim/operators.ts:482-491``).

    Here ``find_char`` returns col 1 (one-after 'h' at col 0). Range is
    therefore ``[1, 8)`` → deletes "ello wo" → result "hrld".
    """

    ctx = _FakeCtx(text="hello world", cursor=Cursor(0, 7))
    change = OperatorFindChange(op="delete", find="T", char="h", count=1)
    replay(change, ctx.as_transition_context())
    assert ctx.text == "hrld"


def test_replay_operator_find_change_dT_at_line_end():
    """``dT<h>`` from col 4 of "hello" (cursor on 'o', last char).

    find_char returns col 1 (one-after 'h' at col 0). Range [1, 5) →
    deletes "ello" → result "h".
    """

    ctx = _FakeCtx(text="hello", cursor=Cursor(0, 4))
    change = OperatorFindChange(op="delete", find="T", char="h", count=1)
    replay(change, ctx.as_transition_context())
    assert ctx.text == "h"


def test_replay_operator_find_change_dt_exclusive():
    """`dt<o>` deletes up to but not including the next 'o'."""

    ctx = _FakeCtx(text="hello world", cursor=Cursor(0, 0))
    change = OperatorFindChange(op="delete", find="t", char="o", count=1)
    replay(change, ctx.as_transition_context())
    # Deletes "hell" — stops one before 'o'.
    assert ctx.text == "o world"


def test_replay_x_change():
    ctx = _FakeCtx(text="hello", cursor=Cursor(0, 1))
    change = XChange(count=1)
    replay(change, ctx.as_transition_context())
    assert ctx.text == "hllo"


def test_replay_toggle_case_change():
    ctx = _FakeCtx(text="hello", cursor=Cursor(0, 0))
    change = ToggleCaseChange(count=3)
    replay(change, ctx.as_transition_context())
    assert ctx.text == "HELlo"


def test_replay_replace_change():
    ctx = _FakeCtx(text="hello", cursor=Cursor(0, 0))
    change = ReplaceChange(char="X", count=2)
    replay(change, ctx.as_transition_context())
    assert ctx.text == "XXllo"


def test_replay_indent_change_right():
    ctx = _FakeCtx(text="hello", cursor=Cursor(0, 0))
    change = IndentChange(dir=">", count=1)
    replay(change, ctx.as_transition_context())
    assert ctx.text == "  hello"


def test_replay_indent_change_left():
    ctx = _FakeCtx(text="    hello", cursor=Cursor(0, 0))
    change = IndentChange(dir="<", count=1)
    replay(change, ctx.as_transition_context())
    assert ctx.text == "  hello"


def test_replay_insert_change():
    ctx = _FakeCtx(text="hello", cursor=Cursor(0, 5))
    change = InsertChange(text=" world")
    replay(change, ctx.as_transition_context())
    assert ctx.text == "hello world"


def test_replay_open_line_below():
    ctx = _FakeCtx(text="line one\nline two", cursor=Cursor(0, 4))
    change = OpenLineChange(direction="below")
    replay(change, ctx.as_transition_context())
    assert ctx.text == "line one\n\nline two"
    assert ctx.cursor == Cursor(1, 0)


def test_replay_open_line_above():
    ctx = _FakeCtx(text="line one\nline two", cursor=Cursor(1, 0))
    change = OpenLineChange(direction="above")
    replay(change, ctx.as_transition_context())
    assert ctx.text == "line one\n\nline two"
    assert ctx.cursor == Cursor(1, 0)


def test_replay_join_change():
    ctx = _FakeCtx(text="line one\nline two", cursor=Cursor(0, 0))
    change = JoinChange(count=1)
    replay(change, ctx.as_transition_context())
    assert ctx.text == "line one line two"


# ---- Integration: dot-repeat through the state machine --------------------


def test_dot_repeat_after_dw_via_state_machine():
    """Full flow: feed `dw` through transition, then `.` replays it."""

    ctx = _FakeCtx(text="hello world there", cursor=Cursor(0, 0))
    txn = ctx.as_transition_context()

    # 1. `d` enters OperatorState.
    r1 = transition(IdleState(), "d", txn)
    assert isinstance(r1.next, OperatorState)

    # 2. `w` executes the operator-motion.
    r2 = transition(r1.next, "w", txn)
    assert r2.execute is not None
    r2.execute()
    assert ctx.text == "world there"
    assert ctx.last_change == OperatorChange(op="delete", motion="w", count=1)

    # 3. `.` in idle replays the last change at the current cursor.
    r3 = transition(IdleState(), ".", txn)
    assert r3.execute is not None
    r3.execute()
    assert ctx.text == "there"


def test_dot_repeat_before_any_change_is_noop():
    ctx = _FakeCtx(text="hello", cursor=Cursor(0, 0))
    result = transition(IdleState(), ".", ctx.as_transition_context())
    assert result.execute is None
    assert ctx.text == "hello"


def test_dot_repeat_after_x():
    ctx = _FakeCtx(text="hello", cursor=Cursor(0, 0))
    txn = ctx.as_transition_context()
    transition(IdleState(), "x", txn).execute()  # type: ignore[misc]
    assert ctx.text == "ello"
    transition(IdleState(), ".", txn).execute()  # type: ignore[misc]
    assert ctx.text == "llo"


def test_dot_repeat_after_replace():
    ctx = _FakeCtx(text="hello", cursor=Cursor(0, 0))
    txn = ctx.as_transition_context()
    # `r!` — replace state then char
    r1 = transition(IdleState(), "r", txn)
    r2 = transition(r1.next, "!", txn)
    r2.execute()  # type: ignore[misc]
    assert ctx.text == "!ello"
    # Move cursor and dot-repeat
    ctx.cursor = Cursor(0, 2)
    transition(IdleState(), ".", txn).execute()  # type: ignore[misc]
    assert ctx.text == "!e!lo"


# ---- Integration: find-repeat through the state machine ------------------


def test_semicolon_repeats_find_forward():
    ctx = _FakeCtx(text="ababab", cursor=Cursor(0, 0))
    txn = ctx.as_transition_context()

    # `fa` finds first 'a' after cursor (at index 2)
    r1 = transition(IdleState(), "f", txn)
    assert isinstance(r1.next, FindState)
    r2 = transition(r1.next, "a", txn)
    r2.execute()  # type: ignore[misc]
    assert ctx.cursor == Cursor(0, 2)
    assert ctx.last_find == ("f", "a")

    # `;` repeats — next 'a' at index 4
    r3 = transition(IdleState(), ";", txn)
    r3.execute()  # type: ignore[misc]
    assert ctx.cursor == Cursor(0, 4)


def test_comma_reverses_find_direction():
    ctx = _FakeCtx(text="abab", cursor=Cursor(0, 0))
    txn = ctx.as_transition_context()

    # `fb` — find next 'b' at index 1
    r1 = transition(IdleState(), "f", txn)
    r2 = transition(r1.next, "b", txn)
    r2.execute()  # type: ignore[misc]
    assert ctx.cursor == Cursor(0, 1)

    # `,` reverses → F → searches backward — no 'b' before index 1
    r3 = transition(IdleState(), ",", txn)
    r3.execute()  # type: ignore[misc]
    # No backward 'b' from index 1 → cursor unchanged
    assert ctx.cursor == Cursor(0, 1)

    # Move cursor right, then `,` again
    ctx.cursor = Cursor(0, 3)  # at second 'b'
    transition(IdleState(), ",", txn).execute()  # type: ignore[misc]
    # F from index 3 finds 'b' at index 1
    assert ctx.cursor == Cursor(0, 1)


# ---- Linewise register flag drives paste behavior -------------------------


def test_linewise_register_after_dd():
    """`dd` sets register_is_linewise=True."""

    ctx = _FakeCtx(text="line one\nline two", cursor=Cursor(0, 0))
    txn = ctx.as_transition_context()
    r1 = transition(IdleState(), "d", txn)
    r2 = transition(r1.next, "d", txn)
    r2.execute()  # type: ignore[misc]
    assert ctx.register_linewise is True
    assert ctx.register_content == "line one\n"


def test_characterwise_register_after_dw():
    """`dw` sets register_is_linewise=False."""

    ctx = _FakeCtx(text="hello world", cursor=Cursor(0, 0))
    txn = ctx.as_transition_context()
    r1 = transition(IdleState(), "d", txn)
    r2 = transition(r1.next, "w", txn)
    r2.execute()  # type: ignore[misc]
    assert ctx.register_linewise is False


# ---- Count multiplication through dot-repeat ------------------------------


def test_dot_repeat_after_3d2w_deletes_six_words():
    """3d2w → effective count 6 → dot-repeat re-deletes 6 words."""

    ctx = _FakeCtx(
        text="a b c d e f g h i j k l m", cursor=Cursor(0, 0)
    )
    txn = ctx.as_transition_context()

    # Manually drive 3 → d → 2 → w
    state = IdleState()
    r = transition(state, "3", txn)
    state = r.next  # CountState
    r = transition(state, "d", txn)
    state = r.next  # OperatorState(count=3)
    r = transition(state, "2", txn)
    state = r.next  # OperatorCountState(count=3, digits="2")
    r = transition(state, "w", txn)
    r.execute()  # type: ignore[misc]
    # 6 words deleted
    assert ctx.text == "g h i j k l m"
    # Recorded change has effective count
    assert ctx.last_change == OperatorChange(op="delete", motion="w", count=6)

    # Now `.` replays — deletes 6 more words
    transition(IdleState(), ".", txn).execute()  # type: ignore[misc]
    assert ctx.text == "m"
