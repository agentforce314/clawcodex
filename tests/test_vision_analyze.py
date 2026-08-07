"""The ``vision_analyze`` tool, its config, and the two guards around it.

Vision for a text-only model has three moving parts and each fails silently
if it breaks, so each gets a test that can actually fail:

* the global-tier config (a half-configured block must read as OFF, not as a
  partial feature),
* the ``Read`` gate (an image block reaching a text-only model is a 400 that
  ends the turn), and
* the fusion re-query handle (a description with no way back is what this
  change exists to fix).

The global config these touch is isolated per test by the autouse
``_isolate_user_permission_settings`` fixture in ``tests/conftest.py``, which
repoints ``config.GLOBAL_CONFIG_DIR`` / ``GLOBAL_CONFIG_FILE`` at ``tmp_path``
— nothing here writes the developer's real ``~/.clawcodex/config.json``.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.providers.vision_config import (
    DEFAULT_VISION_TIMEOUT_MS,
    VISION_KEY,
    load_vision_config,
    save_vision_config,
    set_vision_enabled,
    vision_is_configured,
)
from src.tool_system.context import ToolContext
from src.tool_system.tools.vision_analyze import VisionAnalyzeTool

# A 1x1 PNG — enough for the loader paths; no test here makes a real call.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _write_block(block: object) -> None:
    """Put a raw ``vision`` block in the (isolated) global config."""
    from src import config as cfg_mod

    cfg_mod._get_default_manager().set_global(VISION_KEY, block)


# ---------------------------------------------------------------- config


class TestVisionConfig:
    def test_absent_reads_as_unconfigured(self) -> None:
        assert load_vision_config() is None
        assert vision_is_configured() is False

    def test_round_trip(self) -> None:
        cfg = save_vision_config("openai", "gpt-5.6-luna")
        assert cfg.selector == "openai:gpt-5.6-luna"
        loaded = load_vision_config()
        assert loaded is not None
        assert (loaded.provider, loaded.model) == ("openai", "gpt-5.6-luna")
        assert loaded.timeout_ms == DEFAULT_VISION_TIMEOUT_MS

    @pytest.mark.parametrize(
        "block",
        [
            {"provider": "openai", "model": "gpt-5.6-luna"},  # no enabled key
            {"enabled": True, "provider": "openai"},  # no model
            {"enabled": True, "model": "gpt-5.6-luna"},  # no provider
            {"enabled": True, "provider": "  ", "model": "gpt-5.6-luna"},
            {"enabled": False, "provider": "openai", "model": "gpt-5.6-luna"},
            "not-a-dict",
        ],
    )
    def test_half_configured_reads_as_off(self, block: object) -> None:
        # A partial config is a misconfiguration, not a partial feature —
        # otherwise the tool advertises itself and then fails at call time.
        _write_block(block)
        assert load_vision_config() is None

    @pytest.mark.parametrize("truthy_looking", ["true", "1", "yes", 1])
    def test_enabled_requires_a_real_json_true(self, truthy_looking: object) -> None:
        # bool("false") is True in Python, so a stringly-typed flag would
        # silently arm a paid second provider. hermes guards this the same way.
        _write_block(
            {"enabled": truthy_looking, "provider": "openai", "model": "gpt-5.6-luna"}
        )
        assert load_vision_config() is None

    def test_disable_preserves_the_pairing(self) -> None:
        save_vision_config("openai", "gpt-5.6-luna")
        set_vision_enabled(False)
        assert load_vision_config() is None  # off
        set_vision_enabled(True)
        cfg = load_vision_config()
        assert cfg is not None and cfg.selector == "openai:gpt-5.6-luna"


# ------------------------------------------------------------------ tool


class TestVisionToolGate:
    def test_hidden_until_configured(self) -> None:
        assert VisionAnalyzeTool.is_enabled() is False
        save_vision_config("openai", "gpt-5.6-luna")
        assert VisionAnalyzeTool.is_enabled() is True

    def test_dispatch_without_config_is_a_tool_error_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        # Registry dispatch looks tools up by NAME and bypasses is_enabled,
        # so the in-body check is load-bearing, not redundant.
        ctx = ToolContext(workspace_root=tmp_path, cwd=tmp_path)
        result = VisionAnalyzeTool.call(
            {"image_url": "x.png", "question": "what?"}, ctx
        )
        assert result.is_error
        assert "no vision model configured" in result.output.lower()


class TestVisionToolInput:
    @pytest.fixture(autouse=True)
    def _configured(self):
        save_vision_config("openai", "gpt-5.6-luna")

    def _call(self, tmp_path: Path, image: str) -> object:
        ctx = ToolContext(workspace_root=tmp_path, cwd=tmp_path)
        return VisionAnalyzeTool.call({"image_url": image, "question": "q"}, ctx)

    @pytest.mark.parametrize(
        "image, expected",
        [
            ("https://example.com/a.png", "not a url"),
            ("http://example.com/a.png", "not a url"),
            ("data:image/png;base64,AAAA", "not a data:"),
        ],
    )
    def test_remote_and_data_urls_are_refused_clearly(
        self, tmp_path: Path, image: str, expected: str
    ) -> None:
        # v1 is local-paths-only; fetching remotely needs SSRF defences that
        # are their own change. The refusal must say what to do instead.
        result = self._call(tmp_path, image)
        assert result.is_error
        assert expected in result.output.lower()

    def test_non_image_extension_refused(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("hi")
        result = self._call(tmp_path, str(target))
        assert result.is_error
        assert "not a supported image" in result.output.lower()

    def test_missing_file_refused(self, tmp_path: Path) -> None:
        result = self._call(tmp_path, str(tmp_path / "nope.png"))
        assert result.is_error
        assert "no such image file" in result.output.lower()

    def test_question_reaches_the_vision_prompt(self, tmp_path: Path) -> None:
        # The entire reason this tool beats fusion's fixed pass.
        target = tmp_path / "shot.png"
        target.write_bytes(_PNG_BYTES)
        seen: dict = {}

        def _fake(cfg, source, prompt):
            seen["prompt"] = prompt
            seen["source"] = source
            return "the answer", {"input_tokens": 5, "output_tokens": 7}

        import src.tool_system.tools.vision_analyze as mod

        original = mod._ask_vision_model
        mod._ask_vision_model = _fake
        try:
            ctx = ToolContext(workspace_root=tmp_path, cwd=tmp_path)
            result = VisionAnalyzeTool.call(
                {"image_url": str(target), "question": "what is the VAT total?"}, ctx
            )
        finally:
            mod._ask_vision_model = original

        assert not result.is_error
        assert "what is the VAT total?" in seen["prompt"]
        assert seen["source"]["type"] == "base64"
        assert seen["source"]["media_type"] == "image/png"
        assert "the answer" in result.output


# ------------------------------------------------------------- Read gate


def _ctx_with_provider(tmp_path: Path, provider: object) -> ToolContext:
    ctx = ToolContext(workspace_root=tmp_path, cwd=tmp_path)
    setattr(ctx, "_active_provider", provider)
    return ctx


def _plain_provider(model: str) -> MagicMock:
    p = MagicMock()
    p.model = model
    p.inner = None  # unwrap_provider stops here → not a wrapper
    return p


class TestReadImageGate:
    """``Read`` must not hand an image block to a model that cannot take one."""

    def _read(self, tmp_path: Path, provider: object):
        from src.tool_system.tools.read import _text_only_vision_stub

        return _text_only_vision_stub(tmp_path / "shot.png", _ctx_with_provider(tmp_path, provider))

    def test_text_only_model_gets_a_stub(self, tmp_path: Path) -> None:
        stub = self._read(tmp_path, _plain_provider("deepseek-v4-flash"))
        assert stub is not None
        assert "cannot see images" in stub.output

    def test_stub_names_the_tool_when_vision_is_configured(self, tmp_path: Path) -> None:
        save_vision_config("openai", "gpt-5.6-luna")
        stub = self._read(tmp_path, _plain_provider("deepseek-v4-flash"))
        assert stub is not None
        assert "vision_analyze" in stub.output
        assert "shot.png" in stub.output  # the handle must be actionable

    def test_stub_points_at_setup_when_vision_is_not_configured(
        self, tmp_path: Path
    ) -> None:
        stub = self._read(tmp_path, _plain_provider("deepseek-v4-flash"))
        assert stub is not None
        assert "/vision" in stub.output

    def test_vision_capable_model_is_untouched(self, tmp_path: Path) -> None:
        assert self._read(tmp_path, _plain_provider("claude-opus-5")) is None

    def test_unknown_model_is_untouched(self, tmp_path: Path) -> None:
        # supports_vision only returns False on an exact table hit, so an
        # uncatalogued model keeps today's behaviour rather than silently
        # losing image support.
        assert self._read(tmp_path, _plain_provider("some-new-model-9000")) is None

    def test_no_provider_on_context_is_untouched(self, tmp_path: Path) -> None:
        from src.tool_system.tools.read import _text_only_vision_stub

        ctx = ToolContext(workspace_root=tmp_path, cwd=tmp_path)
        assert _text_only_vision_stub(tmp_path / "shot.png", ctx) is None

    def test_fusion_wrapped_provider_is_NOT_gated(self, tmp_path: Path) -> None:
        """The trap this guard exists for.

        A ``FusionProvider``'s ``.model`` reports the BASE model, which is
        text-only by definition — that is why someone configured fusion. So a
        naive capability check fires on exactly the setup that already works,
        and would strip the image block FusionProvider needs to substitute.
        """
        from src.providers.fusion_models import FusionModel, parse_selector
        from src.providers.fusion_provider import FusionProvider

        fusion = FusionProvider(
            _plain_provider("deepseek-v4-flash"),
            FusionModel(
                name="dsV",
                base=parse_selector("deepseek:deepseek-v4-flash", field="base"),
                vision=parse_selector("openai:gpt-5.6-luna", field="vision"),
            ),
        )
        assert fusion.model == "deepseek-v4-flash"  # the trap, made explicit
        assert self._read(tmp_path, fusion) is None


# --------------------------------------------------- fusion re-query handle


class TestFusionRequeryHandle:
    """Every substituted string must offer a way back to the image.

    hermes appends its handle on failure branches too, and that is where it
    matters most: a failed description is exactly when a second look is worth
    something. The budget-exhausted branch has always told the model to "ask
    about this image on its own" — only with a handle is that actionable.
    """

    def _handle(self, source: dict) -> str:
        from src.providers.fusion_provider import FusionProvider

        return FusionProvider._requery_handle(source)

    def test_silent_when_no_vision_tool_is_configured(self) -> None:
        # An instruction the user cannot satisfy is worse than none.
        assert self._handle({"type": "base64", "data": "AAAA"}) == ""

    def test_base64_source_gets_a_path_free_pointer(self) -> None:
        save_vision_config("openai", "gpt-5.6-luna")
        out = self._handle({"type": "base64", "data": "AAAA"})
        assert "vision_analyze" in out
        assert "file path" in out

    def test_url_source_names_a_step_the_tool_can_actually_serve(self) -> None:
        # hermes writes "use vision_analyze with image_url: <url>" because ITS
        # tool downloads URLs. This port refuses them, so repeating hermes's
        # wording would spend a tool call to earn a refusal.
        save_vision_config("openai", "gpt-5.6-luna")
        out = self._handle({"type": "url", "url": "https://x/y.png"})
        assert "https://x/y.png" in out
        assert "download" in out.lower()
        assert "vision_analyze" in out

    @staticmethod
    def _fusion():
        from src.providers.fusion_models import FusionModel, parse_selector
        from src.providers.fusion_provider import FusionProvider

        inner = MagicMock()
        inner.inner = None
        return FusionProvider(
            inner,
            FusionModel(
                name="dsV",
                base=parse_selector("deepseek:deepseek-v4-flash", field="base"),
                vision=parse_selector("openai:gpt-5.6-luna", field="vision"),
            ),
        )

    def test_every_substituted_branch_carries_the_handle(self) -> None:
        """Drive all five branches for real, not by scraping the source.

        A source-text scan is brittle (it already missed the two multi-line
        branches). Exercising the branches is what actually proves a model
        gets a way back from every one of them.
        """
        import src.providers.fusion_provider as mod
        from src.providers.fusion_provider import _Budget, _Failure

        save_vision_config("openai", "gpt-5.6-luna")
        fusion = self._fusion()
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
        }
        results: dict[str, str] = {}

        # (1) no payload — source carries neither data nor url
        empty = {"type": "image", "source": {"type": "base64", "media_type": "image/png"}}
        results["no-payload"] = fusion._substitute(empty, _Budget(4))["text"]

        # (2) cached failure  and  (3) cache hit — seed the module cache
        key = mod._image_key(fusion._scope(), block["source"])
        mod._cache_put(key, _Failure("boom"))
        results["cached-failure"] = fusion._substitute(block, _Budget(4))["text"]
        mod._cache_put(key, "a description")
        results["cache-hit"] = fusion._substitute(block, _Budget(4))["text"]

        # (4) budget exhausted — a fresh key so the cache does not short-circuit
        other = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "BBBB"},
        }
        results["exhausted"] = fusion._substitute(other, _Budget(0))["text"]

        # (5) live failure — a third key, with the vision call raising
        third = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "CCCC"},
        }
        original = fusion._describe
        fusion._describe = MagicMock(side_effect=RuntimeError("provider down"))
        try:
            results["live-failure"] = fusion._substitute(third, _Budget(4))["text"]
        finally:
            fusion._describe = original

        assert len(results) == 5
        missing = [name for name, text in results.items() if "vision_analyze" not in text]
        assert not missing, (
            "these substituted branches offer the model no way back to the "
            f"image: {missing}\n"
            + "\n".join(f"  {k}: {v!r}" for k, v in results.items())
        )

    def test_branches_stay_silent_without_a_vision_tool(self) -> None:
        # Same five paths, no vision configured: no dangling instruction.
        from src.providers.fusion_provider import _Budget

        fusion = self._fusion()
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "ZZZZ"},
        }
        text = fusion._substitute(block, _Budget(0))["text"]
        assert "vision_analyze" not in text


# ------------------------------------------------ Read gate, through the tool


class TestReadToolCallSite:
    """The gate must be wired into ``Read``, not merely implemented.

    Testing ``_text_only_vision_stub`` alone proves nothing: deleting the
    call site in ``_read_call`` leaves such tests green while the feature is
    entirely dead. These drive ``ReadTool.call``.
    """

    def _png(self, tmp_path: Path) -> Path:
        target = tmp_path / "shot.png"
        target.write_bytes(_PNG_BYTES)
        return target

    def test_text_only_model_gets_text_not_an_image_block(self, tmp_path: Path) -> None:
        from src.tool_system.tools.read import ReadTool

        target = self._png(tmp_path)
        ctx = _ctx_with_provider(tmp_path, _plain_provider("deepseek-v4-flash"))
        result = ReadTool.call({"file_path": str(target)}, ctx)
        assert isinstance(result.output, str), (
            "a text-only model must not receive an image block — that is the "
            "400 this gate exists to prevent"
        )
        assert "cannot see images" in result.output

    def test_vision_capable_model_still_gets_the_image(self, tmp_path: Path) -> None:
        from src.tool_system.tools.read import ReadTool

        target = self._png(tmp_path)
        ctx = _ctx_with_provider(tmp_path, _plain_provider("claude-opus-5"))
        result = ReadTool.call({"file_path": str(target)}, ctx)
        assert isinstance(result.output, dict)
        assert result.output.get("type") == "image"

    def test_fusion_still_gets_the_image_to_substitute(self, tmp_path: Path) -> None:
        from src.providers.fusion_models import FusionModel, parse_selector
        from src.providers.fusion_provider import FusionProvider
        from src.tool_system.tools.read import ReadTool

        fusion = FusionProvider(
            _plain_provider("deepseek-v4-flash"),
            FusionModel(
                name="dsV",
                base=parse_selector("deepseek:deepseek-v4-flash", field="base"),
                vision=parse_selector("openai:gpt-5.6-luna", field="vision"),
            ),
        )
        target = self._png(tmp_path)
        result = ReadTool.call({"file_path": str(target)}, _ctx_with_provider(tmp_path, fusion))
        assert isinstance(result.output, dict) and result.output.get("type") == "image"

    def test_pdf_pages_stub_does_not_name_a_tool_that_refuses_pdfs(
        self, tmp_path: Path
    ) -> None:
        # Through ReadTool.call, like its siblings: the PDF branch is a
        # SECOND call site, and testing the helper alone would leave it as
        # dead as the image branch was.
        #
        # vision_analyze accepts only image extensions, so naming it for a
        # .pdf would cost a tool call and return a hard refusal.
        from src.tool_system.tools.read import ReadTool

        target = tmp_path / "doc.pdf"
        # Minimal and never parsed: the gate returns before extraction.
        target.write_bytes(b"%PDF-1.4\n%stub\n")
        ctx = _ctx_with_provider(tmp_path, _plain_provider("deepseek-v4-flash"))
        result = ReadTool.call({"file_path": str(target), "pages": "1-2"}, ctx)
        assert isinstance(result.output, str), result.output
        assert "vision_analyze" not in result.output
        assert "pdftotext" in result.output


# ------------------------------------------------------------ /vision command


class TestVisionCommand:
    def _run(self, arg: str):
        from src.command_system.types import CommandContext
        from src.command_system.vision_command import vision_command_call

        ctx = CommandContext(
            workspace_root=Path("."), cwd=Path("."), conversation=None,
            cost_tracker=None, history=None,
        )
        return vision_command_call(arg, ctx).value

    def test_bare_reports_unconfigured_then_configured(self) -> None:
        assert "not configured" in self._run("")
        save_vision_config("openai", "gpt-5.6-luna")
        assert "openai:gpt-5.6-luna" in self._run("")

    def test_off_then_on_round_trips_without_retyping(self) -> None:
        save_vision_config("openai", "gpt-5.6-luna")
        assert "disabled" in self._run("off")
        assert load_vision_config() is None
        assert "enabled" in self._run("on").lower()
        cfg = load_vision_config()
        assert cfg is not None and cfg.selector == "openai:gpt-5.6-luna"

    def test_on_without_a_saved_pairing_does_not_claim_success(self) -> None:
        out = self._run("on")
        assert "no saved vision model" in out.lower()
        # Assert the RAW block, not load_vision_config(): the both-halves
        # rule rejects {"enabled": true} on its own, so a load-based
        # assertion returns None whether or not the un-flip happened and
        # cannot distinguish fixed from broken.
        from src import config as cfg_mod

        raw = cfg_mod._get_default_manager().load_global().get(VISION_KEY)
        assert not (isinstance(raw, dict) and raw.get("enabled")), (
            f"left a half-armed config behind: {raw!r}"
        )

    def test_missing_colon_is_rejected(self) -> None:
        assert "Usage" in self._run("openai")
        assert load_vision_config() is None

    def test_empty_half_is_rejected(self) -> None:
        assert "Both halves" in self._run("openai:")
        assert load_vision_config() is None

    def test_unknown_provider_is_rejected_even_with_no_providers_configured(
        self,
    ) -> None:
        # The isolated config has no providers map. Validating against it
        # alone would let ANY string through and persist a config that can
        # never work, while advertising the tool as available.
        out = self._run("definitely-not-a-provider:some-model")
        assert "Unknown provider" in out
        assert load_vision_config() is None

    def test_known_provider_is_accepted(self) -> None:
        out = self._run("openai:gpt-5.6-luna")
        assert "set to openai:gpt-5.6-luna" in out
        cfg = load_vision_config()
        assert cfg is not None and cfg.selector == "openai:gpt-5.6-luna"

    def test_non_vision_model_warns_but_still_saves(self) -> None:
        out = self._run("deepseek:deepseek-v4-flash")
        assert "NOT vision-capable" in out
        assert load_vision_config() is not None


class TestSaveDoesNotClobberHandEditedFields:
    def test_prompt_and_timeout_survive_a_repoint(self) -> None:
        # No command sets these, so they are hand-edited — silently wiping
        # them on the next /vision would destroy user config without a word.
        _write_block(
            {
                "enabled": True,
                "provider": "openai",
                "model": "gpt-5.6-luna",
                "prompt": "always transcribe numbers digit by digit",
                "timeout_ms": 12_345,
            }
        )
        save_vision_config("zai", "glm-5v-turbo")
        cfg = load_vision_config()
        assert cfg is not None
        assert cfg.selector == "zai:glm-5v-turbo"
        assert cfg.prompt == "always transcribe numbers digit by digit"
        assert cfg.timeout_ms == 12_345


class TestImagePipelineReuse:
    """Magic bytes and downscaling — the two halves of the media/size fix.

    Both were correct but unpinned: reverting either left the whole file
    green, so the next refactor of ``_load_image`` would silently
    reintroduce a confusing provider 400.
    """

    @staticmethod
    def _source(tmp_path: Path, name: str, raw: bytes) -> dict:
        target = tmp_path / name
        target.write_bytes(raw)
        save_vision_config("openai", "gpt-5.6-luna")
        seen: dict = {}

        def _fake(cfg, source, prompt):
            seen["source"] = source
            return "ok", {}

        import src.tool_system.tools.vision_analyze as mod

        original = mod._ask_vision_model
        mod._ask_vision_model = _fake
        try:
            ctx = ToolContext(workspace_root=tmp_path, cwd=tmp_path)
            result = VisionAnalyzeTool.call(
                {"image_url": str(target), "question": "q"}, ctx
            )
        finally:
            mod._ask_vision_model = original
        assert not result.is_error, result.output
        return seen["source"]

    def test_media_type_comes_from_magic_bytes_not_the_extension(
        self, tmp_path: Path
    ) -> None:
        # A JPEG saved as .png must ship image/jpeg. Trusting the filename
        # sends a mismatched media_type and the provider 400s with an error
        # that reads like a vendor outage.
        from io import BytesIO

        from PIL import Image

        buf = BytesIO()
        Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="JPEG")
        source = self._source(tmp_path, "shot.png", buf.getvalue())
        assert source["media_type"] == "image/jpeg"

    def test_oversized_images_are_downscaled_before_shipping(
        self, tmp_path: Path
    ) -> None:
        # The API ceiling is 5 MB base64 / 3.75 MB raw, and a retina
        # screenshot — the case this feature exists for — exceeds it.
        from io import BytesIO

        from PIL import Image
        from src.utils.image_processor import IMAGE_MAX_WIDTH

        buf = BytesIO()
        Image.new("RGB", (IMAGE_MAX_WIDTH * 3, 400), (200, 40, 40)).save(
            buf, format="PNG"
        )
        raw = buf.getvalue()
        source = self._source(tmp_path, "big.png", raw)
        shipped = base64.b64decode(source["data"])
        assert len(shipped) < len(raw), (
            f"image was shipped at full size ({len(raw)} bytes) — "
            "maybe_resize_image is not being applied"
        )
