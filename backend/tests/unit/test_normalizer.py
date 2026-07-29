import unittest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from evidence_platform.modules.retrieval.normalization.normalizer import QueryNormalizer


class TestQueryNormalizer(unittest.TestCase):

    def setUp(self):
        self.normalizer = QueryNormalizer()

    def test_basic_cleaning(self):
        query = "  In HER2-low   breast  cancer\n  "
        norm = self.normalizer.normalize(query)
        self.assertEqual(norm.raw_query, query)
        # Should compress whitespaces
        self.assertTrue(norm.normalized_query.startswith("In HER2-low (human epidermal growth factor receptor 2) breast cancer"))

    def test_acronym_expansion(self):
        query = "does T-DXd improve PFS in mBC?"
        norm = self.normalizer.normalize(query)
        
        # Verify expansions exist in expanded_terms
        self.assertIn("T-DXd", norm.expanded_terms)
        self.assertIn("PFS", norm.expanded_terms)
        self.assertIn("mBC", norm.expanded_terms)
        
        self.assertEqual(norm.expanded_terms["T-DXd"], "trastuzumab deruxtecan")
        self.assertEqual(norm.expanded_terms["PFS"], "progression-free survival")
        self.assertEqual(norm.expanded_terms["mBC"], "metastatic breast cancer")

        # Verify expansions are injected in context in the query string
        self.assertIn("T-DXd (trastuzumab deruxtecan)", norm.normalized_query)
        self.assertIn("PFS (progression-free survival)", norm.normalized_query)
        self.assertIn("mBC (metastatic breast cancer)", norm.normalized_query)

    def test_case_insensitivity(self):
        query_lower = "t-dxd and pfs and mbc"
        query_upper = "T-DXD AND PFS AND MBC"
        
        norm_lower = self.normalizer.normalize(query_lower)
        norm_upper = self.normalizer.normalize(query_upper)
        
        self.assertIn("t-dxd (trastuzumab deruxtecan)", norm_lower.normalized_query.lower())
        self.assertIn("t-dxd (trastuzumab deruxtecan)", norm_upper.normalized_query.lower())


if __name__ == "__main__":
    unittest.main()
