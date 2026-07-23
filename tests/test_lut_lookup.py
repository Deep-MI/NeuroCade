"""Test lut lookup behavior for NeuroCade."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.runtime_tools.lut import get_by_id, resolve_label, search_lut  # noqa: E402


def _write_lut(path: Path) -> None:
    """Write a minimal LUT fixture with names and annotations."""
    path.write_text(
        "\n".join(
            [
                "#No. Label Name: R G B A",
                "17 Left-Hippocampus 220 216 20 0 # left/LH hippocampus (HP)",
                "192 Corpus_Callosum 170 255 255 0 # corpus callosum (CC)",
                "251 CC_Posterior 0 0 64 0 # posterior corpus callosum (CC)",
                "252 CC_Mid_Posterior 0 0 112 0 # mid-posterior corpus callosum (CC)",
                "253 CC_Central 0 0 160 0 # central corpus callosum (CC)",
                "254 CC_Mid_Anterior 0 0 208 0 # mid-anterior corpus callosum (CC)",
                "255 CC_Anterior 0 0 255 0 # anterior corpus callosum (CC)",
                "1004 ctx-lh-corpuscallosum 120 70 50 0 # left/LH cortical gray matter: corpus callosum (CC)",
            ]
        ),
        encoding="utf-8",
    )


def test_search_lut_matches_annotated_comments_for_corpus_callosum(tmp_path):
    lut_path = tmp_path / "FreeSurferColorLUT.txt"
    _write_lut(lut_path)

    matches, total = search_lut("corpus callosum", lut_path=str(lut_path))
    ids = [match.label_id for match in matches]

    assert total >= 7
    assert ids[0] == 192
    assert {251, 252, 253, 254, 255}.issubset(ids)
    assert matches[1].comment == "posterior corpus callosum (CC)"


def test_search_lut_tokenizes_short_lut_names_and_annotations(tmp_path):
    lut_path = tmp_path / "FreeSurferColorLUT.txt"
    _write_lut(lut_path)

    matches, _ = search_lut("callosum", lut_path=str(lut_path))
    ids = [match.label_id for match in matches]

    assert ids[0] == 192
    assert 251 in ids
    assert 1004 in ids


def test_search_lut_can_filter_to_allowed_label_ids(tmp_path):
    lut_path = tmp_path / "FreeSurferColorLUT.txt"
    _write_lut(lut_path)

    matches, total = search_lut("corpus callosum", lut_path=str(lut_path), allowed_label_ids={251, 17})

    assert total == 1
    assert [(match.label_id, match.name) for match in matches] == [(251, "CC_Posterior")]


def test_resolve_label_uses_ranked_annotation_search(tmp_path):
    lut_path = tmp_path / "FreeSurferColorLUT.txt"
    _write_lut(lut_path)

    assert resolve_label("corpus callosum", str(lut_path)) == (192, "Corpus_Callosum")
    assert resolve_label("left hippocampus", str(lut_path)) == (17, "Left-Hippocampus")
    assert resolve_label("251", str(lut_path)) == (251, "CC_Posterior")


def test_lut_cache_is_scoped_by_path(tmp_path):
    lut_a = tmp_path / "a.txt"
    lut_b = tmp_path / "b.txt"
    lut_a.write_text("1 Alpha 0 0 0 0 # first file\n", encoding="utf-8")
    lut_b.write_text("2 Alpha 0 0 0 0 # second file\n", encoding="utf-8")

    assert get_by_id(1, str(lut_a)) == "Alpha"
    assert get_by_id(1, str(lut_b)) is None
    assert get_by_id(2, str(lut_b)) == "Alpha"
