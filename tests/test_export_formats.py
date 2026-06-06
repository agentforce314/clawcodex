"""Tests for :mod:`src.utils.export_formats`.

Faithful port of ``typescript/src/utils/exportFormats.test.ts`` (242 lines). The
TS suite drives the same six public helpers; Python keeps the assertions
one-to-one, only translating ``null`` -> ``None``, the ``preserveMarkdownExtension``
option object -> the ``preserve_markdown_extension`` keyword, and the parsed
result objects -> :class:`~src.utils.export_formats.ParsedExportArgs` field reads.
"""

from __future__ import annotations

from src.utils.export_formats import (
    ParsedExportArgs,
    ensure_export_filename_extension,
    extension_for_export_format,
    infer_export_format_from_filename,
    normalize_export_format,
    parse_export_args,
    resolve_export_filepath,
)


# --------------------------------------------------------------------------- #
# normalize_export_format
# --------------------------------------------------------------------------- #


class TestNormalizeExportFormat:
    def test_normalizes_text_variants(self) -> None:
        assert normalize_export_format("text") == "text"
        assert normalize_export_format("txt") == "text"
        assert normalize_export_format("TEXT") == "text"
        assert normalize_export_format(" TxT ") == "text"

    def test_normalizes_markdown_variants(self) -> None:
        assert normalize_export_format("markdown") == "markdown"
        assert normalize_export_format("md") == "markdown"
        assert normalize_export_format("Markdown") == "markdown"
        assert normalize_export_format(" MD ") == "markdown"

    def test_normalizes_json(self) -> None:
        assert normalize_export_format("json") == "json"
        assert normalize_export_format("JSON") == "json"
        assert normalize_export_format(" Json ") == "json"

    def test_returns_none_for_unknown(self) -> None:
        assert normalize_export_format("xml") is None
        assert normalize_export_format("") is None
        assert normalize_export_format("csv") is None


# --------------------------------------------------------------------------- #
# infer_export_format_from_filename
# --------------------------------------------------------------------------- #


class TestInferExportFormatFromFilename:
    def test_infers_from_txt(self) -> None:
        assert infer_export_format_from_filename("a.txt") == "text"

    def test_infers_from_md(self) -> None:
        assert infer_export_format_from_filename("a.md") == "markdown"

    def test_infers_from_markdown(self) -> None:
        assert infer_export_format_from_filename("a.markdown") == "markdown"

    def test_infers_from_json(self) -> None:
        assert infer_export_format_from_filename("a.json") == "json"

    def test_returns_none_for_no_extension(self) -> None:
        assert infer_export_format_from_filename("a") is None

    def test_returns_none_for_unknown_extension(self) -> None:
        assert infer_export_format_from_filename("a.csv") is None


# --------------------------------------------------------------------------- #
# extension_for_export_format
# --------------------------------------------------------------------------- #


class TestExtensionForExportFormat:
    def test_returns_txt_for_text(self) -> None:
        assert extension_for_export_format("text") == ".txt"

    def test_returns_md_for_markdown(self) -> None:
        assert extension_for_export_format("markdown") == ".md"

    def test_returns_json_for_json(self) -> None:
        assert extension_for_export_format("json") == ".json"


# --------------------------------------------------------------------------- #
# ensure_export_filename_extension
# --------------------------------------------------------------------------- #


class TestEnsureExportFilenameExtension:
    def test_adds_extension_when_none(self) -> None:
        assert ensure_export_filename_extension("a", "text") == "a.txt"

    def test_replaces_wrong_extension_for_markdown(self) -> None:
        assert ensure_export_filename_extension("a.json", "markdown") == "a.md"

    def test_replaces_wrong_extension_for_json(self) -> None:
        assert ensure_export_filename_extension("a.txt", "json") == "a.json"

    def test_handles_name_with_dots(self) -> None:
        assert (
            ensure_export_filename_extension("my.conversation.txt", "json")
            == "my.conversation.json"
        )

    def test_normalizes_trailing_dot_filenames_without_duplicating_separators(
        self,
    ) -> None:
        assert ensure_export_filename_extension("conversation.", "json") == "conversation.json"

    def test_does_not_treat_dots_in_directory_names_as_filename_extensions(
        self,
    ) -> None:
        assert (
            ensure_export_filename_extension("logs.v1/transcript", "json")
            == "logs.v1/transcript.json"
        )

    def test_does_not_treat_a_dotfile_name_as_an_extension_only_filename(self) -> None:
        assert ensure_export_filename_extension(".env", "text") == ".env.txt"

    def test_preserves_correct_extension(self) -> None:
        assert ensure_export_filename_extension("a.md", "markdown") == "a.md"

    def test_preserves_supported_markdown_extension(self) -> None:
        assert (
            ensure_export_filename_extension(
                "a.markdown", "markdown", preserve_markdown_extension=True
            )
            == "a.markdown"
        )

    def test_canonicalizes_markdown_to_md_by_default(self) -> None:
        assert ensure_export_filename_extension("a.markdown", "markdown") == "a.md"


# --------------------------------------------------------------------------- #
# parse_export_args
# --------------------------------------------------------------------------- #


class TestParseExportArgs:
    def test_empty_args(self) -> None:
        assert parse_export_args("") == ParsedExportArgs()

    def test_filename_only(self) -> None:
        assert parse_export_args("transcript.txt") == ParsedExportArgs(
            filename="transcript.txt"
        )

    def test_format_json_with_filename(self) -> None:
        assert parse_export_args("--format json transcript") == ParsedExportArgs(
            format="json", filename="transcript"
        )

    def test_f_md_with_filename(self) -> None:
        assert parse_export_args("-f md transcript.txt") == ParsedExportArgs(
            format="markdown", filename="transcript.txt"
        )

    def test_format_flag_overrides_extension(self) -> None:
        result = parse_export_args("--format json transcript.txt")
        assert result.format == "json"
        assert result.filename == "transcript.txt"

    def test_unsupported_format_returns_error(self) -> None:
        result = parse_export_args("--format xml transcript")
        assert result.error is not None
        assert "Unsupported export format: xml" in result.error

    def test_unsupported_dash_prefixed_option_returns_error(self) -> None:
        result = parse_export_args("--formt json transcript")
        assert result.error is not None
        assert "Unsupported export option: --formt" in result.error

    def test_missing_format_value_returns_error(self) -> None:
        result = parse_export_args("--format")
        assert result.error is not None
        assert "Missing value for --format" in result.error

    def test_f_short_form(self) -> None:
        assert parse_export_args("-f text conversation") == ParsedExportArgs(
            format="text", filename="conversation"
        )

    def test_format_markdown(self) -> None:
        assert parse_export_args("--format markdown output") == ParsedExportArgs(
            format="markdown", filename="output"
        )

    def test_preserves_filenames_with_spaces(self) -> None:
        assert parse_export_args("my conversation.txt") == ParsedExportArgs(
            filename="my conversation.txt"
        )

    def test_preserves_filename_with_spaces_and_format_flag(self) -> None:
        assert parse_export_args("--format json my conversation") == ParsedExportArgs(
            format="json", filename="my conversation"
        )

    def test_format_flag_after_filename(self) -> None:
        assert parse_export_args("output --format json") == ParsedExportArgs(
            format="json", filename="output"
        )

    def test_parses_quoted_filenames(self) -> None:
        assert parse_export_args('--format json "my conversation"') == ParsedExportArgs(
            format="json", filename="my conversation"
        )

    def test_treats_quoted_flag_looking_tokens_as_filenames(self) -> None:
        assert parse_export_args('"--format"') == ParsedExportArgs(filename="--format")

    def test_supports_double_dash_terminator_for_dash_leading_filenames(self) -> None:
        assert parse_export_args("-- --format") == ParsedExportArgs(filename="--format")

    def test_parses_quoted_format_values(self) -> None:
        assert parse_export_args('--format "md" transcript') == ParsedExportArgs(
            format="markdown", filename="transcript"
        )

    def test_reports_unterminated_quoted_strings(self) -> None:
        result = parse_export_args('--format json "my conversation')
        assert result.error is not None
        assert "Unterminated quoted string" in result.error


# --------------------------------------------------------------------------- #
# resolve_export_filepath
# --------------------------------------------------------------------------- #


class TestResolveExportFilepath:
    def test_resolves_relative_export_filenames_under_cwd(self) -> None:
        assert (
            resolve_export_filepath("/work/project", "transcript.md")
            == "/work/project/transcript.md"
        )

    def test_preserves_absolute_export_filenames(self) -> None:
        assert (
            resolve_export_filepath("/work/project", "/tmp/transcript.md")
            == "/tmp/transcript.md"
        )
