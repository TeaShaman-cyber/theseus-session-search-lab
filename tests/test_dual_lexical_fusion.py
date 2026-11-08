import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FUSION_PATH = ROOT / "experiments" / "017-dual-lexical-view-fusion" / "fusion.py"


def load_fusion():
    spec = importlib.util.spec_from_file_location("dual_lexical_fusion", FUSION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def row(session_id, ordinal, text_fp):
    return {
        "session_id": session_id,
        "ordinal": ordinal,
        "text_fp": text_fp,
    }


class DualLexicalFusionTest(unittest.TestCase):
    def test_overlap_accumulates_rrf_score(self):
        fusion = load_fusion()
        a = row("s1", 1, "a")
        b = row("s2", 2, "b")
        c = row("s3", 3, "c")

        out = fusion.fuse_rrf([a, b], [c, a], k=60, limit=10)

        self.assertEqual(out[0]["text_fp"], "a")
        self.assertEqual(out[0]["full_rank"], 1)
        self.assertEqual(out[0]["sparse_rank"], 2)
        self.assertAlmostEqual(out[0]["rrf_score"], 1 / 61 + 1 / 62)

    def test_single_view_candidates_are_preserved(self):
        fusion = load_fusion()
        a = row("s1", 1, "a")
        b = row("s2", 2, "b")

        out = fusion.fuse_rrf([a], [b], k=60, limit=10)

        self.assertEqual({x["text_fp"] for x in out}, {"a", "b"})

    def test_same_content_in_different_rows_is_not_deduplicated(self):
        fusion = load_fusion()
        a1 = row("base", 1, "same")
        a2 = row("branch", 1, "same")

        out = fusion.fuse_rrf([a1], [a2], k=60, limit=10)

        self.assertEqual(len(out), 2)
        self.assertEqual([x["session_id"] for x in out], ["base", "branch"])

    def test_tie_break_is_deterministic_and_prefers_full_rank(self):
        fusion = load_fusion()
        a = row("z", 1, "a")
        b = row("a", 1, "b")

        out = fusion.fuse_rrf([a], [b], k=60, limit=10)

        self.assertEqual(out[0]["text_fp"], "a")
        self.assertEqual(out[0]["full_rank"], 1)
        self.assertIsNone(out[0]["sparse_rank"])

    def test_invalid_parameters_fail_closed(self):
        fusion = load_fusion()
        with self.assertRaisesRegex(ValueError, "RRF_K_INVALID"):
            fusion.fuse_rrf([], [], k=0, limit=10)
        with self.assertRaisesRegex(ValueError, "RRF_LIMIT_INVALID"):
            fusion.fuse_rrf([], [], k=60, limit=0)


if __name__ == "__main__":
    unittest.main()
