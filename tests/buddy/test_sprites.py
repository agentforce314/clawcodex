"""Sprite library: rendering invariants over all 18 species."""
from __future__ import annotations

import pytest

from src.buddy.sprites import (
    BODIES,
    HAT_LINES,
    MIN_COLS_FOR_FULL_SPRITE,
    render_face,
    render_sprite,
    sprite_frame_count,
)
from src.buddy.types import CompanionBones, EYES, HATS, SPECIES


@pytest.mark.parametrize("species", SPECIES)
def test_eye_substituted_in_all_frames(species: str) -> None:
    """No remaining ``{E}`` tokens after rendering."""
    bones = CompanionBones(
        rarity='common', species=species, eye='·', hat='none',
        shiny=False, stats={},
    )
    for frame in range(3):
        lines = render_sprite(bones, frame=frame)
        for line in lines:
            assert '{E}' not in line, (
                f"{species} frame {frame}: {{E}} not substituted"
            )


@pytest.mark.parametrize("species", SPECIES)
def test_eye_glyph_appears(species: str) -> None:
    """The eye glyph should appear somewhere in the rendered output."""
    bones = CompanionBones(
        rarity='common', species=species, eye='@', hat='none',
        shiny=False, stats={},
    )
    lines = render_sprite(bones, frame=0)
    assert any('@' in line for line in lines), (
        f"{species}: eye glyph not found in rendered sprite"
    )


@pytest.mark.parametrize("species", SPECIES)
def test_line_count_with_hat_none(species: str) -> None:
    """With ``hat='none'``, line count is 4 IF every frame has blank
    line 0 (shift fires), else 5."""
    frames = BODIES[species]
    all_frames_blank_top = all(not f[0].strip() for f in frames)
    bones = CompanionBones(
        rarity='common', species=species, eye='·', hat='none',
        shiny=False, stats={},
    )
    lines = render_sprite(bones, frame=0)
    expected = 4 if all_frames_blank_top else 5
    assert len(lines) == expected, (
        f"{species}: expected {expected} lines (all_blank_top={all_frames_blank_top}), got {len(lines)}"
    )


@pytest.mark.parametrize("species", SPECIES)
def test_line_count_with_hat_keeps_5(species: str) -> None:
    """With any hat other than 'none', no shift; line count stays 5."""
    bones = CompanionBones(
        rarity='rare', species=species, eye='·', hat='crown',
        shiny=False, stats={},
    )
    # Skip species whose frame-0 line 0 is non-blank (hat won't fit;
    # render_sprite leaves line 0 alone in that case).
    if BODIES[species][0][0].strip():
        pytest.skip(f"{species} frame-0 line 0 is non-blank")
    lines = render_sprite(bones, frame=0)
    assert len(lines) == 5
    assert 'crown' not in lines[0]  # crown content, not name
    assert lines[0] == HAT_LINES['crown']


@pytest.mark.parametrize("species", SPECIES)
def test_width_bounded(species: str) -> None:
    """All rendered lines have width ≤ 12 (sprite-body width)."""
    bones = CompanionBones(
        rarity='common', species=species, eye='·', hat='none',
        shiny=False, stats={},
    )
    for frame in range(3):
        for line in render_sprite(bones, frame=frame):
            assert len(line) <= 12, (
                f"{species} frame {frame}: line width {len(line)} > 12: {line!r}"
            )


@pytest.mark.parametrize("species", SPECIES)
def test_frame_count_is_3(species: str) -> None:
    assert sprite_frame_count(species) == 3


@pytest.mark.parametrize("species", SPECIES)
def test_render_face_contains_eye(species: str) -> None:
    """The compact face for each species contains the eye glyph."""
    for eye in EYES:
        bones = CompanionBones(
            rarity='common', species=species, eye=eye, hat='none',
            shiny=False, stats={},
        )
        face = render_face(bones)
        assert eye in face, (
            f"{species}: eye {eye!r} not in face {face!r}"
        )
        assert '{E}' not in face


def test_min_cols_constant() -> None:
    """MIN_COLS_FOR_FULL_SPRITE matches TS const."""
    assert MIN_COLS_FOR_FULL_SPRITE == 100


def test_hat_lines_has_all_hats() -> None:
    """Every Hat literal must have a HAT_LINES entry."""
    assert set(HAT_LINES.keys()) == set(HATS)


def test_bodies_has_all_species() -> None:
    assert set(BODIES.keys()) == set(SPECIES)


# Content snapshot — one line per species per frame, sourced from TS
# typescript/src/buddy/sprites.ts (BODIES table). Catches single-character
# drifts that the structural tests above miss (e.g. mushroom's
# .-o-OO-o-. vs .-O-oo-O-. alternation pattern).
_EXPECTED_BODIES: dict[str, tuple[tuple[str, ...], ...]] = {
    'duck': (
        ('            ', '    __      ', '  <({E} )___  ', '   (  ._>   ', '    `--´    '),
        ('            ', '    __      ', '  <({E} )___  ', '   (  ._>   ', '    `--´~   '),
        ('            ', '    __      ', '  <({E} )___  ', '   (  .__>  ', '    `--´    '),
    ),
    'goose': (
        ('            ', '     ({E}>    ', '     ||     ', '   _(__)_   ', '    ^^^^    '),
        ('            ', '    ({E}>     ', '     ||     ', '   _(__)_   ', '    ^^^^    '),
        ('            ', '     ({E}>>   ', '     ||     ', '   _(__)_   ', '    ^^^^    '),
    ),
    'blob': (
        ('            ', '   .----.   ', '  ( {E}  {E} )  ', '  (      )  ', '   `----´   '),
        ('            ', '  .------.  ', ' (  {E}  {E}  ) ', ' (        ) ', '  `------´  '),
        ('            ', '    .--.    ', '   ({E}  {E})   ', '   (    )   ', '    `--´    '),
    ),
    'cat': (
        ('            ', '   /\\_/\\    ', '  ( {E}   {E})  ', '  (  ω  )   ', '  (")_(")   '),
        ('            ', '   /\\_/\\    ', '  ( {E}   {E})  ', '  (  ω  )   ', '  (")_(")~  '),
        ('            ', '   /\\-/\\    ', '  ( {E}   {E})  ', '  (  ω  )   ', '  (")_(")   '),
    ),
    'dragon': (
        ('            ', '  /^\\  /^\\  ', ' <  {E}  {E}  > ', ' (   ~~   ) ', '  `-vvvv-´  '),
        ('            ', '  /^\\  /^\\  ', ' <  {E}  {E}  > ', ' (        ) ', '  `-vvvv-´  '),
        ('   ~    ~   ', '  /^\\  /^\\  ', ' <  {E}  {E}  > ', ' (   ~~   ) ', '  `-vvvv-´  '),
    ),
    'octopus': (
        ('            ', '   .----.   ', '  ( {E}  {E} )  ', '  (______)  ', '  /\\/\\/\\/\\  '),
        ('            ', '   .----.   ', '  ( {E}  {E} )  ', '  (______)  ', '  \\/\\/\\/\\/  '),
        ('     o      ', '   .----.   ', '  ( {E}  {E} )  ', '  (______)  ', '  /\\/\\/\\/\\  '),
    ),
    'owl': (
        ('            ', '   /\\  /\\   ', '  (({E})({E}))  ', '  (  ><  )  ', '   `----´   '),
        ('            ', '   /\\  /\\   ', '  (({E})({E}))  ', '  (  ><  )  ', '   .----.   '),
        ('            ', '   /\\  /\\   ', '  (({E})(-))  ', '  (  ><  )  ', '   `----´   '),
    ),
    'penguin': (
        ('            ', '  .---.     ', '  ({E}>{E})     ', ' /(   )\\    ', '  `---´     '),
        ('            ', '  .---.     ', '  ({E}>{E})     ', ' |(   )|    ', '  `---´     '),
        ('  .---.     ', '  ({E}>{E})     ', ' /(   )\\    ', '  `---´     ', '   ~ ~      '),
    ),
    'turtle': (
        ('            ', '   _,--._   ', '  ( {E}  {E} )  ', ' /[______]\\ ', '  ``    ``  '),
        ('            ', '   _,--._   ', '  ( {E}  {E} )  ', ' /[______]\\ ', '   ``  ``   '),
        ('            ', '   _,--._   ', '  ( {E}  {E} )  ', ' /[======]\\ ', '  ``    ``  '),
    ),
    'snail': (
        ('            ', ' {E}    .--.  ', '  \\  ( @ )  ', '   \\_`--´   ', '  ~~~~~~~   '),
        ('            ', '  {E}   .--.  ', '  |  ( @ )  ', '   \\_`--´   ', '  ~~~~~~~   '),
        ('            ', ' {E}    .--.  ', '  \\  ( @  ) ', '   \\_`--´   ', '   ~~~~~~   '),
    ),
    'ghost': (
        ('            ', '   .----.   ', '  / {E}  {E} \\  ', '  |      |  ', '  ~`~``~`~  '),
        ('            ', '   .----.   ', '  / {E}  {E} \\  ', '  |      |  ', '  `~`~~`~`  '),
        ('    ~  ~    ', '   .----.   ', '  / {E}  {E} \\  ', '  |      |  ', '  ~~`~~`~~  '),
    ),
    'axolotl': (
        ('            ', '}~(______)~{', '}~({E} .. {E})~{', '  ( .--. )  ', '  (_/  \\_)  '),
        ('            ', '~}(______){~', '~}({E} .. {E}){~', '  ( .--. )  ', '  (_/  \\_)  '),
        ('            ', '}~(______)~{', '}~({E} .. {E})~{', '  (  --  )  ', '  ~_/  \\_~  '),
    ),
    'capybara': (
        ('            ', '  n______n  ', ' ( {E}    {E} ) ', ' (   oo   ) ', '  `------´  '),
        ('            ', '  n______n  ', ' ( {E}    {E} ) ', ' (   Oo   ) ', '  `------´  '),
        ('    ~  ~    ', '  u______n  ', ' ( {E}    {E} ) ', ' (   oo   ) ', '  `------´  '),
    ),
    'cactus': (
        ('            ', ' n  ____  n ', ' | |{E}  {E}| | ', ' |_|    |_| ', '   |    |   '),
        ('            ', '    ____    ', ' n |{E}  {E}| n ', ' |_|    |_| ', '   |    |   '),
        (' n        n ', ' |  ____  | ', ' | |{E}  {E}| | ', ' |_|    |_| ', '   |    |   '),
    ),
    'robot': (
        ('            ', '   .[||].   ', '  [ {E}  {E} ]  ', '  [ ==== ]  ', '  `------´  '),
        ('            ', '   .[||].   ', '  [ {E}  {E} ]  ', '  [ -==- ]  ', '  `------´  '),
        ('     *      ', '   .[||].   ', '  [ {E}  {E} ]  ', '  [ ==== ]  ', '  `------´  '),
    ),
    'rabbit': (
        ('            ', '   (\\__/)   ', '  ( {E}  {E} )  ', ' =(  ..  )= ', '  (")__(")  '),
        ('            ', '   (|__/)   ', '  ( {E}  {E} )  ', ' =(  ..  )= ', '  (")__(")  '),
        ('            ', '   (\\__/)   ', '  ( {E}  {E} )  ', ' =( .  . )= ', '  (")__(")  '),
    ),
    'mushroom': (
        ('            ', ' .-o-OO-o-. ', '(__________)', '   |{E}  {E}|   ', '   |____|   '),
        ('            ', ' .-O-oo-O-. ', '(__________)', '   |{E}  {E}|   ', '   |____|   '),
        ('   . o  .   ', ' .-o-OO-o-. ', '(__________)', '   |{E}  {E}|   ', '   |____|   '),
    ),
    'chonk': (
        ('            ', '  /\\    /\\  ', ' ( {E}    {E} ) ', ' (   ..   ) ', '  `------´  '),
        ('            ', '  /\\    /|  ', ' ( {E}    {E} ) ', ' (   ..   ) ', '  `------´  '),
        ('            ', '  /\\    /\\  ', ' ( {E}    {E} ) ', ' (   ..   ) ', '  `------´~ '),
    ),
}


@pytest.mark.parametrize("species", SPECIES)
def test_bodies_match_ts_source(species: str) -> None:
    """Content snapshot: every line of every frame of every species
    matches the expected value sourced from
    ``typescript/src/buddy/sprites.ts``. Catches single-character
    drifts that the structural tests miss.
    """
    expected = _EXPECTED_BODIES[species]
    actual = BODIES[species]
    assert len(actual) == len(expected), (
        f"{species}: frame count drift (got {len(actual)}, expected {len(expected)})"
    )
    for frame_idx, (exp_frame, act_frame) in enumerate(zip(expected, actual)):
        assert len(act_frame) == len(exp_frame), (
            f"{species} frame {frame_idx}: line count drift"
        )
        for line_idx, (exp_line, act_line) in enumerate(zip(exp_frame, act_frame)):
            assert act_line == exp_line, (
                f"{species} frame {frame_idx} line {line_idx} drift:\n"
                f"  expected: {exp_line!r}\n"
                f"  actual:   {act_line!r}"
            )


def test_expected_bodies_covers_all_species() -> None:
    """Guard: snapshot table must cover every species."""
    assert set(_EXPECTED_BODIES.keys()) == set(SPECIES)


def test_penguin_stays_5_lines_with_no_hat() -> None:
    """Penguin's frame 2 uses line 0 for the `~ ~` waddle, so the
    shift should NOT fire even with hat='none'. Regression check for
    the ALL-frames vs SOME-frames distinction in render_sprite."""
    bones = CompanionBones(
        rarity='common', species='penguin', eye='·', hat='none',
        shiny=False, stats={},
    )
    lines = render_sprite(bones, frame=0)
    assert len(lines) == 5
