"""
Unit tests for benchmark.py pure functions.

Run from repo root:
    .venv/bin/pip install pytest
    .venv/bin/pytest tests/

These tests deliberately stay away from real OCR (Tesseract, OCRmyPDF, RapidOCR) —
those are integration concerns covered by `python benchmark.py --doctor` + a quick run.
What we test here are the bits that have failed in subtle ways during development:
sort/slug behavior, metric normalization, page-discovery filtering, mode presets.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the repo root importable regardless of where pytest is run from.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import benchmark  # noqa: E402
from benchmark import (  # noqa: E402
    IMAGE_EXTS,
    MODES,
    PDF_EXTS,
    POLISH_DIACRITICS,
    _diacritic_only,
    _normalize_for_metrics,
    _numbers_only,
    _resolve_tessdata,
    compute_metrics,
    ground_truth_for,
    list_pages,
    slugify,
)


# ---------- slugify ----------

class TestSlugify:
    def test_spaces_become_underscores(self):
        assert slugify("hello world") == "hello_world"

    def test_preserves_polish_diacritics(self):
        assert slugify("Wydział Psychologii") == "Wydział_Psychologii"

    def test_keeps_dots_and_dashes(self):
        # Important: dots are part of the regex allowlist, otherwise "COVID-19 vs Szczepienia"
        # would lose the version separator semantics callers may rely on.
        assert "." in slugify("file.v1.txt") or slugify("file.v1.txt") == "file.v1.txt"
        assert "-" in slugify("a-b-c")

    def test_strips_leading_trailing_underscores_and_dots(self):
        assert not slugify("...weird...").startswith("_")
        assert not slugify("...weird...").startswith(".")
        assert not slugify("___trail___").endswith("_")

    def test_empty_input_returns_fallback(self):
        # Implementation contract: returns "doc" rather than empty string, so file
        # writes don't blow up later.
        assert slugify("") == "doc"
        assert slugify("///") == "doc"

    def test_real_world_doc_names(self):
        # These are the actual folder names we ship under input/. Regression-guard them.
        assert slugify("COVID-19 vs Szczepienia - JPG") == "COVID-19_vs_Szczepienia_-_JPG"
        assert slugify("pismo-odreczne-pl") == "pismo-odreczne-pl"

    def test_collision_is_possible(self):
        # Documents a known collision. If this ever stops being true, slugify changed
        # behavior and TROUBLESHOOTING.md needs updating.
        assert slugify("A B") == slugify("A_B") == "A_B"


# ---------- _normalize_for_metrics ----------

class TestNormalizeForMetrics:
    def test_collapses_internal_whitespace(self):
        assert _normalize_for_metrics("foo   bar\t\tbaz") == "foo bar baz"

    def test_caps_blank_lines_at_two(self):
        assert _normalize_for_metrics("a\n\n\n\n\nb") == "a\n\nb"

    def test_normalizes_line_endings(self):
        assert _normalize_for_metrics("a\r\nb\rc") == "a\nb\nc"

    def test_strips_outer_whitespace(self):
        assert _normalize_for_metrics("\n\n  hi  \n\n") == "hi"

    def test_preserves_diacritics(self):
        # The normalizer must NOT touch unicode content — that would silently improve
        # CER on lossy comparisons.
        assert "ą" in _normalize_for_metrics("ąćęłńóśźż")


# ---------- _diacritic_only ----------

class TestDiacriticOnly:
    def test_keeps_only_polish_diacritics(self):
        # Only the 9 Polish diacritics survive; ASCII letters (including the 'a' and 'i')
        # are stripped. "Załóżmy ćmę dziś" → łóżćęś.
        assert _diacritic_only("Załóżmy ćmę dziś") == "łóżćęś"

    def test_strips_ascii_and_punctuation(self):
        assert _diacritic_only("Hello, World! 123") == ""

    def test_handles_uppercase(self):
        assert _diacritic_only("ŻÓŁĆ") == "ŻÓŁĆ"

    def test_diacritic_set_is_complete(self):
        # Sanity: all 9 Polish letters in both cases = 18 chars.
        assert len(POLISH_DIACRITICS) == 18


# ---------- _numbers_only ----------

class TestNumbersOnly:
    def test_extracts_integers(self):
        assert _numbers_only("Page 42 of 100") == ["42", "100"]

    def test_extracts_decimals_and_commas(self):
        assert _numbers_only("Cena: 19,99 zł lub 19.99 USD") == ["19,99", "19.99"]

    def test_handles_run_on_digits(self):
        assert _numbers_only("PESEL 12345678901") == ["12345678901"]

    def test_no_digits_returns_empty(self):
        assert _numbers_only("nothing here") == []

    def test_year_in_text(self):
        assert "2024" in _numbers_only("rok 2024 to był dobry rok")


# ---------- compute_metrics ----------

class TestComputeMetrics:
    def test_identical_strings_yield_zero_cer(self):
        m = compute_metrics("Witaj świecie", "Witaj świecie")
        assert m["cer"] == pytest.approx(0.0)
        assert m["wer"] == pytest.approx(0.0)

    def test_diacritic_only_difference_shows_in_diacritic_cer(self):
        # ASCII-stripped hypothesis: CER should be modest, but diacritic_cer should be 1.0
        # (every diacritic in the reference is missing).
        ref = "Załóżmy że tak"
        hyp = "Zalozmy ze tak"
        m = compute_metrics(hyp, ref)
        assert m["diacritic_cer"] == pytest.approx(1.0)
        assert m["cer"] < 0.5  # most chars still match

    def test_empty_reference_returns_empty_dict(self):
        # Without a reference there's nothing to score — must not crash.
        assert compute_metrics("anything", "") == {}

    def test_number_accuracy_full_match(self):
        m = compute_metrics("Suma 123 i 456", "Total 123 plus 456")
        assert m["number_accuracy"] == pytest.approx(1.0)

    def test_number_accuracy_partial(self):
        m = compute_metrics("Suma 123 i 999", "Total 123 plus 456")
        # 1 of 2 reference numbers found
        assert m["number_accuracy"] == pytest.approx(0.5)

    def test_number_accuracy_omitted_when_no_numbers_in_reference(self):
        m = compute_metrics("hello world", "witaj świecie")
        assert "number_accuracy" not in m

    def test_lengths_reported(self):
        m = compute_metrics("abc", "abcd")
        assert m["len_hyp_chars"] == 3
        assert m["len_ref_chars"] == 4


# ---------- list_pages ----------

class TestListPages:
    def test_extension_filter(self, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"")
        (tmp_path / "b.PDF").write_bytes(b"")  # case-insensitive
        (tmp_path / "c.txt").write_text("not an image")
        (tmp_path / "d.docx").write_bytes(b"")
        pages = list_pages(tmp_path)
        names = [p.name for p in pages]
        assert "a.jpg" in names
        assert "b.PDF" in names
        assert "c.txt" not in names
        assert "d.docx" not in names

    def test_skips_hidden_files(self, tmp_path):
        (tmp_path / ".DS_Store").write_bytes(b"")
        (tmp_path / ".hidden.jpg").write_bytes(b"")
        (tmp_path / "real.jpg").write_bytes(b"")
        names = [p.name for p in list_pages(tmp_path)]
        assert names == ["real.jpg"]

    def test_recurses_subfolders(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.jpg").write_bytes(b"")
        (tmp_path / "top.jpg").write_bytes(b"")
        names = [p.name for p in list_pages(tmp_path)]
        assert "deep.jpg" in names and "top.jpg" in names

    def test_sort_is_alphabetical_known_footgun(self, tmp_path):
        # Documents the page-order footgun called out in TROUBLESHOOTING.md.
        for n in ("page-1.jpg", "page-2.jpg", "page-10.jpg"):
            (tmp_path / n).write_bytes(b"")
        ordered = [p.name for p in list_pages(tmp_path)]
        # If this ever changes to natural sort, update the troubleshooting doc.
        assert ordered == ["page-1.jpg", "page-10.jpg", "page-2.jpg"]

    def test_empty_dir(self, tmp_path):
        assert list_pages(tmp_path) == []

    def test_recognized_extensions_are_a_known_set(self):
        # Pinning the contract so adding/removing extensions is a conscious choice.
        # HEIC is intentionally excluded — see TROUBLESHOOTING section "no pages found".
        assert ".jpg" in IMAGE_EXTS
        assert ".pdf" in PDF_EXTS
        assert ".heic" not in IMAGE_EXTS
        assert ".docx" not in IMAGE_EXTS


# ---------- ground_truth_for ----------

class TestGroundTruth:
    def test_returns_none_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(benchmark, "GT_DIR", tmp_path)
        assert ground_truth_for("anything") is None

    def test_returns_text_when_present(self, monkeypatch, tmp_path):
        monkeypatch.setattr(benchmark, "GT_DIR", tmp_path)
        (tmp_path / "doc.txt").write_text("reference text", encoding="utf-8")
        assert ground_truth_for("doc") == "reference text"

    def test_handles_polish_filename(self, monkeypatch, tmp_path):
        monkeypatch.setattr(benchmark, "GT_DIR", tmp_path)
        name = "Wydział_dyplom"
        (tmp_path / f"{name}.txt").write_text("ąćę", encoding="utf-8")
        assert ground_truth_for(name) == "ąćę"


# ---------- _resolve_tessdata ----------

class TestResolveTessdata:
    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("TESSDATA_BEST_DIR", raising=False)
        assert _resolve_tessdata("best") is None

    def test_returns_path_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TESSDATA_BEST_DIR", str(tmp_path))
        assert _resolve_tessdata("best") == tmp_path

    def test_fast_variant_uses_fast_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TESSDATA_FAST_DIR", str(tmp_path))
        monkeypatch.delenv("TESSDATA_BEST_DIR", raising=False)
        assert _resolve_tessdata("fast") == tmp_path
        assert _resolve_tessdata("best") is None

    def test_does_not_validate_existence(self, monkeypatch):
        # Intentional: tesseract reports the error itself. But this also explains
        # the silent-fallback footgun in TROUBLESHOOTING.md.
        monkeypatch.setenv("TESSDATA_BEST_DIR", "/nonexistent/path/here")
        assert _resolve_tessdata("best") == Path("/nonexistent/path/here")


# ---------- MODES sanity ----------

class TestModes:
    def test_all_modes_have_required_keys(self):
        required = {"methods", "max_pages", "metrics", "desc"}
        for name, cfg in MODES.items():
            missing = required - set(cfg.keys())
            assert not missing, f"mode {name!r} missing keys: {missing}"

    def test_quick_mode_skips_csv(self):
        # Regression guard: quick mode used to write inflated metrics to results.csv
        # because OCR is capped at max_pages=2 but GT covers the whole doc.
        assert MODES["quick"]["metrics"] is False
        assert MODES["quick"]["max_pages"] == 2

    def test_production_mode_is_single_method_no_csv(self):
        # Production runs are for end-users wanting artifacts, not metric noise.
        assert MODES["production"]["methods"] == ["ocrmypdf"]
        assert MODES["production"]["metrics"] is False

    def test_full_mode_means_all_methods(self):
        # An empty methods list means "all available".
        assert MODES["full"]["methods"] == []
        assert MODES["full"]["metrics"] is True

    def test_compare_methods_are_subset_of_known(self):
        known = {"tess_best_psm1", "tess_best_psm4", "tess_best_psm6",
                 "tess_fast_psm1", "ocrmypdf", "rapidocr"}
        assert set(MODES["compare"]["methods"]) <= known
