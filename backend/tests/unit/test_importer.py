import unittest
import sys
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from evidence_platform.db.models.models import (
    Base,
    SourceDocument,
    DocumentRevision,
    DocumentSection,
    EvidenceUnit,
    Chunk,
)
from evidence_platform.modules.ingestion.importer import CorpusImporter


class TestCorpusImporter(unittest.TestCase):

    def setUp(self):
        # Use an in-memory SQLite database for schema compatibility unit testing
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.importer = CorpusImporter(self.session)

        # Standard valid mock manifest item
        self.valid_item = {
            "source": "pmc",
            "pmid": "9999999",
            "pmcid": "PMC9999999",
            "doi": "10.1000/xyz999",
            "title": "Oncology Test Publication",
            "authors": ["Dr. Smith", "Dr. Rao"],
            "journal": "Oncology Letters",
            "publication_date": "2025-08-14",
            "canonical_url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9999999/",
            "display_rights": True,
            "retraction_status": "not_retracted",
            "content_sha256": "f0e4c2f76c58916ec258f246851bea091d14d4207eb481e19eb4cf16cb52b850",
            "parser_name": "jats_parser",
            "parser_version": "1.0.0",
            "source_artifact_uri": "file:///path/to/PMC9999999.xml",
            "study_type": "rct",
            "mesh_terms": ["Humans", "Oncology"],
            "clinical_trial_ids": ["NCT99999999"],
            "sections": [
                {
                    "section_path": ["Methods"],
                    "section_kind": "methods",
                    "ordinal": 1,
                    "text": "This is methods text. We evaluated trastuzumab deruxtecan here. This study was human.",
                    "source_locator": {
                        "kind": "xpath",
                        "locator": {"xpath": "/article/body/sec[1]"}
                    }
                }
            ],
            "tables": []
        }

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_import_valid_item(self):
        doc_id = self.importer.import_manifest_item(self.valid_item)
        
        # Verify SourceDocument insertion
        doc = self.session.query(SourceDocument).filter_by(id=doc_id).first()
        self.assertIsNotNone(doc)
        self.assertEqual(doc.pmcid, "PMC9999999")
        self.assertEqual(doc.title, "Oncology Test Publication")
        self.assertTrue(doc.display_rights)

        # Verify DocumentRevision insertion
        rev = self.session.query(DocumentRevision).filter_by(document_id=doc_id).first()
        self.assertIsNotNone(rev)
        self.assertEqual(rev.status, "published")
        self.assertEqual(rev.study_type, "rct")

        # Verify DocumentSection insertion
        sec = self.session.query(DocumentSection).filter_by(revision_id=rev.id).first()
        self.assertIsNotNone(sec)
        self.assertEqual(sec.section_kind, "methods")
        self.assertEqual(sec.source_locator["kind"], "xpath")

        # Verify EvidenceUnit & Chunk insertion
        eu = self.session.query(EvidenceUnit).filter_by(section_id=sec.id).first()
        self.assertIsNotNone(eu)
        
        chunk = self.session.query(Chunk).filter_by(evidence_unit_id=eu.id).first()
        self.assertIsNotNone(chunk)
        self.assertEqual(chunk.index_status, "pending")

    def test_import_forbidden_display_rights(self):
        # Set display rights to False
        item_no_rights = dict(self.valid_item)
        item_no_rights["display_rights"] = False
        item_no_rights["pmcid"] = "PMC8888888"
        item_no_rights["pmid"] = "8888888"
        
        doc_id = self.importer.import_manifest_item(item_no_rights)
        
        # Verify status is citation_not_ready and index_status of chunk is failed/excluded
        rev = self.session.query(DocumentRevision).filter_by(document_id=doc_id).first()
        self.assertEqual(rev.status, "citation_not_ready")
        
        sec = self.session.query(DocumentSection).filter_by(revision_id=rev.id).first()
        eu = self.session.query(EvidenceUnit).filter_by(section_id=sec.id).first()
        chunk = self.session.query(Chunk).filter_by(evidence_unit_id=eu.id).first()
        self.assertEqual(chunk.index_status, "failed")

    def test_import_missing_locator(self):
        # Set locator to 'none' to represent missing coordinates/xpath
        item_no_loc = dict(self.valid_item)
        item_no_loc["pmcid"] = "PMC7777777"
        item_no_loc["pmid"] = "7777777"
        item_no_loc["sections"] = [
            {
                "section_path": ["Methods"],
                "section_kind": "methods",
                "ordinal": 1,
                "text": "This is methods text.",
                "source_locator": {
                    "kind": "none",
                    "locator": {}
                }
            }
        ]
        
        doc_id = self.importer.import_manifest_item(item_no_loc)
        
        # Verify status is citation_not_ready since locator is missing (none)
        rev = self.session.query(DocumentRevision).filter_by(document_id=doc_id).first()
        self.assertEqual(rev.status, "citation_not_ready")


if __name__ == "__main__":
    unittest.main()
