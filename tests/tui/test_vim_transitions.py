"""Tests for ``src/tui/vim_state.py`` — discriminated `CommandState`
union and pure `transition()` function.

Phase 1 of the ch14 refactor. Covers all 11 state variants + the count
clamp + the purity invariant (calling transition twice yields the same
(next, side-effects-determined)).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.tui.vim_buffer import Cursor
from src.tui.vim_persistent import (
    OperatorChange,
    PersistentState,
    RecordedChange,
    ReplaceChange,
    XChange,
)
from src.tui.vim_state import (
    MAX_VIM_COUNT,
    CountState,
    FindState,
    GState,
    IdleState,
    IndentState,
    OperatorCountState,
    OperatorFindState,
    OperatorGState,
    OperatorState,
    OperatorTextObjState,
    ReplaceState,
    TransitionContext,
    transition,
)


@dataclass
class _FakeCtx:
    """Dict-backed ``TransitionContext`` for tests.

    Captures all primitives without touching Textual or a real buffer.
    Stores enough state that executors can mutate it and tests can
    inspect the result.
    """

    text: str = "hello world"
    cursor: Cursor = field(default_factory=lambda: Cursor(0, 0))
    register_content: str = ""
    register_linewise: bool = False
    last_find: tuple[str, str] | None = None
    last_change: RecordedChange | None = None
    changes_recorded: list[RecordedChange] = field(default_factory=list)
    entered_insert_at: int | None = None

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
            enter_insert=lambda offset: setattr(self, "entered_insert_at", offset),
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


# ---- Idle state -----------------------------------------------------------


def test_idle_count_enters_count_state():
    ctx = _FakeCtx().as_transition_context()
    result = transition(IdleState(), "3", ctx)
    assert result.next == CountState(digits="3")
    assert result.execute is None


def test_idle_zero_is_motion_not_count():
    """'0' in idle is the start-of-line motion, NOT a count digit."""

    ctx = _FakeCtx()
    result = transition(IdleState(), "0", ctx.as_transition_context())
    # Stays in idle; executes the motion.
    assert result.next is None
    assert result.execute is not None
    result.execute()
    assert ctx.cursor == Cursor(0, 0)


def test_idle_d_enters_operator_state():
    ctx = _FakeCtx().as_transition_context()
    result = transition(IdleState(), "d", ctx)
    assert result.next == OperatorState(op="delete", count=1)


def test_idle_c_enters_operator_state_change():
    ctx = _FakeCtx().as_transition_context()
    result = transition(IdleState(), "c", ctx)
    assert result.next == OperatorState(op="change", count=1)


def test_idle_y_enters_operator_state_yank():
    ctx = _FakeCtx().as_transition_context()
    result = transition(IdleState(), "y", ctx)
    assert result.next == OperatorState(op="yank", count=1)


def test_idle_f_enters_find_state():
    ctx = _FakeCtx().as_transition_context()
    result = transition(IdleState(), "f", ctx)
    assert result.next == FindState(find="f", count=1)


def test_idle_g_enters_g_state():
    ctx = _FakeCtx().as_transition_context()
    result = transition(IdleState(), "g", ctx)
    assert result.next == GState(count=1)


def test_idle_r_enters_replace_state():
    ctx = _FakeCtx().as_transition_context()
    result = transition(IdleState(), "r", ctx)
    assert result.next == ReplaceState(count=1)


def test_idle_indent_enters_indent_state():
    ctx = _FakeCtx().as_transition_context()
    result = transition(IdleState(), ">", ctx)
    assert result.next == IndentState(dir=">", count=1)


def test_idle_simple_motion_executes_immediately():
    ctx = _FakeCtx()
    ctx.cursor = Cursor(0, 2)
    result = transition(IdleState(), "l", ctx.as_transition_context())
    assert result.next is None
    assert result.execute is not None
    result.execute()
    assert ctx.cursor.col == 3


def test_idle_e_motion_lands_on_last_word_char():
    """``e`` lands on the LAST char of the word, not one past."""

    ctx = _FakeCtx(text="hello world", cursor=Cursor(0, 0))
    result = transition(IdleState(), "e", ctx.as_transition_context())
    assert result.execute is not None
    result.execute()
    # 'hello' ends at col 4 (the 'o'). 'e' must land ON the 'o', not the
    # space at col 5.
    assert ctx.cursor == Cursor(0, 4)


def test_idle_w_motion_lands_at_word_start():
    """``w`` lands AT the start of the next word."""

    ctx = _FakeCtx(text="hello world", cursor=Cursor(0, 0))
    result = transition(IdleState(), "w", ctx.as_transition_context())
    assert result.execute is not None
    result.execute()
    assert ctx.cursor == Cursor(0, 6)


def test_idle_x_executes_immediately_and_records():
    ctx = _FakeCtx(text="hello", cursor=Cursor(0, 1))
    result = transition(IdleState(), "x", ctx.as_transition_context())
    assert result.execute is not None
    result.execute()
    assert ctx.text == "hllo"
    assert ctx.changes_recorded == [XChange(count=1)]


def test_idle_unknown_key_stays_idle():
    ctx = _FakeCtx().as_transition_context()
    result = transition(IdleState(), "Z", ctx)
    assert result.next is None
    assert result.execute is None


# ---- Count state ----------------------------------------------------------


def test_count_accumulates_digits():
    ctx = _FakeCtx().as_transition_context()
    result = transition(CountState(digits="3"), "5", ctx)
    assert result.next == CountState(digits="35")


def test_count_saturates_at_max():
    ctx = _FakeCtx().as_transition_context()
    result = transition(CountState(digits="9999"), "9", ctx)
    # 99999 saturates to 10000
    assert result.next == CountState(digits=str(MAX_VIM_COUNT))


def test_count_then_operator_carries_count():
    ctx = _FakeCtx().as_transition_context()
    result = transition(CountState(digits="3"), "d", ctx)
    assert result.next == OperatorState(op="delete", count=3)


def test_count_then_motion_executes_with_count():
    ctx = _FakeCtx(text="abcdef", cursor=Cursor(0, 0))
    result = transition(CountState(digits="3"), "l", ctx.as_transition_context())
    assert result.next == IdleState()
    assert result.execute is not None
    result.execute()
    assert ctx.cursor.col == 3


# ---- Operator state -------------------------------------------------------


def test_operator_self_repeat_is_line_op():
    """``dd`` enters line-wise delete."""

    ctx = _FakeCtx(text="line one\nline two", cursor=Cursor(0, 0))
    result = transition(
        OperatorState(op="delete", count=1), "d", ctx.as_transition_context()
    )
    assert result.next == IdleState()
    assert result.execute is not None
    result.execute()
    assert ctx.text == "line two"
    assert ctx.register_linewise is True


def test_operator_count_after_d_enters_operator_count():
    ctx = _FakeCtx().as_transition_context()
    result = transition(OperatorState(op="delete", count=1), "2", ctx)
    assert result.next == OperatorCountState(op="delete", count=1, digits="2")


def test_operator_text_obj_scope():
    ctx = _FakeCtx().as_transition_context()
    result = transition(OperatorState(op="delete", count=1), "i", ctx)
    assert result.next == OperatorTextObjState(
        op="delete", count=1, scope="inner"
    )


def test_operator_find_state():
    ctx = _FakeCtx().as_transition_context()
    result = transition(OperatorState(op="delete", count=1), "f", ctx)
    assert result.next == OperatorFindState(op="delete", count=1, find="f")


def test_operator_motion_executes_operator_motion():
    ctx = _FakeCtx(text="hello world", cursor=Cursor(0, 0))
    result = transition(
        OperatorState(op="delete", count=1), "w", ctx.as_transition_context()
    )
    assert result.next == IdleState()
    assert result.execute is not None
    result.execute()
    assert ctx.text == "world"
    assert ctx.register_content == "hello "
    # Recorded change supports dot-repeat
    assert any(
        isinstance(c, OperatorChange) and c.motion == "w" for c in ctx.changes_recorded
    )


# ---- OperatorCount state --------------------------------------------------


def test_operator_count_multiplies_counts():
    """3d2w → count=6 (matches TS transitions.ts:328 effectiveCount)."""

    ctx = _FakeCtx().as_transition_context()
    # We arrived at OperatorCountState(op='delete', count=3, digits='2');
    # next key 'w' executes with effective count 6.
    state = OperatorCountState(op="delete", count=3, digits="2")
    result = transition(state, "w", ctx)
    assert result.next == IdleState()
    # Verify by inspecting what would be recorded — execute and check
    fake_ctx = _FakeCtx(text="a b c d e f g", cursor=Cursor(0, 0))
    result = transition(state, "w", fake_ctx.as_transition_context())
    result.execute()  # type: ignore[misc]
    recorded = fake_ctx.changes_recorded[-1]
    assert isinstance(recorded, OperatorChange)
    assert recorded.count == 6


def test_operator_count_accumulates_digits():
    ctx = _FakeCtx().as_transition_context()
    result = transition(
        OperatorCountState(op="delete", count=3, digits="2"), "5", ctx
    )
    assert result.next == OperatorCountState(op="delete", count=3, digits="25")


def test_operator_count_multiplication_saturates():
    """``9999d9999w`` saturates the effective count at MAX_VIM_COUNT."""

    ctx = _FakeCtx(text="a b c d e", cursor=Cursor(0, 0))
    state = OperatorCountState(op="delete", count=9999, digits="9999")
    result = transition(state, "w", ctx.as_transition_context())
    assert result.execute is not None
    result.execute()
    # 9999 * 9999 = 99,980,001 → saturates to MAX_VIM_COUNT.
    recorded = ctx.changes_recorded[-1]
    assert recorded.count == MAX_VIM_COUNT


# ---- Find / OperatorFind / Replace states ---------------------------------


def test_find_state_executes_motion():
    ctx = _FakeCtx(text="hello world", cursor=Cursor(0, 0))
    result = transition(
        FindState(find="f", count=1), "o", ctx.as_transition_context()
    )
    assert result.next == IdleState()
    result.execute()  # type: ignore[misc]
    assert ctx.cursor == Cursor(0, 4)
    assert ctx.last_find == ("f", "o")


def test_operator_find_executes_delete_through_char():
    ctx = _FakeCtx(text="change me\"end", cursor=Cursor(0, 0))
    result = transition(
        OperatorFindState(op="change", count=1, find="f"),
        '"',
        ctx.as_transition_context(),
    )
    assert result.next == IdleState()
    result.execute()  # type: ignore[misc]
    # 'cf"' deletes from cursor through the quote (inclusive).
    assert ctx.text == "end"


def test_replace_state_replaces_char():
    ctx = _FakeCtx(text="hello", cursor=Cursor(0, 1))
    result = transition(
        ReplaceState(count=1), "X", ctx.as_transition_context()
    )
    assert result.next == IdleState()
    result.execute()  # type: ignore[misc]
    assert ctx.text == "hXllo"


# ---- G state --------------------------------------------------------------


def test_g_gg_goes_to_buffer_start():
    ctx = _FakeCtx(text="line one\nline two\nline three", cursor=Cursor(2, 5))
    result = transition(GState(count=1), "g", ctx.as_transition_context())
    assert result.next == IdleState()
    result.execute()  # type: ignore[misc]
    assert ctx.cursor == Cursor(0, 0)


def test_operator_g_dgg_deletes_to_top():
    ctx = _FakeCtx(text="line one\nline two\nline three", cursor=Cursor(2, 0))
    result = transition(
        OperatorGState(op="delete", count=1), "g", ctx.as_transition_context()
    )
    assert result.next == IdleState()
    result.execute()  # type: ignore[misc]
    # Deletes lines 0..2 inclusive — everything.
    assert ctx.text == ""


def test_operator_g_dgg_sets_linewise_register():
    """``dgg`` records the register as linewise."""

    ctx = _FakeCtx(text="line one\nline two", cursor=Cursor(1, 0))
    result = transition(
        OperatorGState(op="delete", count=1), "g", ctx.as_transition_context()
    )
    result.execute()  # type: ignore[misc]
    assert ctx.register_linewise is True


# ---- Indent state ---------------------------------------------------------


def test_indent_state_double_arrow_indents():
    ctx = _FakeCtx(text="hello", cursor=Cursor(0, 0))
    result = transition(
        IndentState(dir=">", count=1), ">", ctx.as_transition_context()
    )
    assert result.next == IdleState()
    result.execute()  # type: ignore[misc]
    assert ctx.text == "  hello"


# ---- Purity invariant -----------------------------------------------------


def test_transition_is_pure():
    """Calling transition twice with the same args yields the same TransitionResult.

    This is the property-test purity check from G1.
    """

    # Use a state that's not derived from ctx-dependent execution.
    state = IdleState()
    key = "d"
    ctx1 = _FakeCtx().as_transition_context()
    ctx2 = _FakeCtx().as_transition_context()
    r1 = transition(state, key, ctx1)
    r2 = transition(state, key, ctx2)
    assert r1.next == r2.next


def test_transition_does_not_mutate_state():
    """``transition()`` does not mutate the input state (it's frozen anyway)."""

    state = OperatorState(op="delete", count=1)
    ctx = _FakeCtx().as_transition_context()
    _result = transition(state, "w", ctx)
    # The frozen dataclass refuses mutation, so this is a hard guarantee.
    assert state == OperatorState(op="delete", count=1)


# ---- Frozen dataclass invariant -------------------------------------------


def test_all_states_are_frozen():
    import dataclasses

    import src.tui.vim_state as vs

    for state_cls in [
        vs.IdleState,
        vs.CountState,
        vs.OperatorState,
        vs.OperatorCountState,
        vs.OperatorFindState,
        vs.OperatorTextObjState,
        vs.FindState,
        vs.GState,
        vs.OperatorGState,
        vs.ReplaceState,
        vs.IndentState,
    ]:
        assert dataclasses.is_dataclass(state_cls)
        # Frozen check: attempting to mutate raises FrozenInstanceError.
        params = state_cls.__dataclass_params__  # type: ignore[attr-defined]
        assert params.frozen is True, f"{state_cls.__name__} not frozen"
