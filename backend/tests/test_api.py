"""API integration tests.

Uses FastAPI TestClient — no network, Gemini fully mocked.

Coverage:
  - Full flow: upload → extract → patch → analyze → get
  - Bad file type rejected (413 / 415)
  - Oversize file rejected (413)
  - Samples load without any Gemini call
  - PII fields absent from stored analysis record
  - /healthz returns 200
"""

from __future__ import annotations

import io
import json
import struct
import zlib
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

# Reset in-memory stores before each test
from app.services import storage as _storage


# ---------------------------------------------------------------------------
# Minimal valid binary fixtures
# ---------------------------------------------------------------------------

def _make_pdf() -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj\n<</Type /Catalog>>\nendobj\n"
        b"xref\n0 1\n0000000000 65535 f \ntrailer\n<</Size 1>>\n"
        b"startxref\n9\n%%EOF"
    )


def _make_jpeg() -> bytes:
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xd9"
    )


def _make_png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
    idat_data = zlib.compress(b"\x00\xff\xff\xff")
    idat_crc = zlib.crc32(b"IDAT" + idat_data) & 0xFFFFFFFF
    idat = struct.pack(">I", len(idat_data)) + b"IDAT" + idat_data + struct.pack(">I", idat_crc)
    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
    return sig + ihdr + idat + iend


# ---------------------------------------------------------------------------
# Fixture: a minimal valid KFSExtraction dict (matches the Pydantic model)
# ---------------------------------------------------------------------------

def _minimal_extraction_dict() -> dict:
    ev = lambda v: {"value": v, "page": 1, "quote": "test quote", "confidence": 0.99, "status": "found"}
    ev_none = lambda: {"value": None, "page": None, "quote": None, "confidence": None, "status": "not_found"}
    return {
        "doc_meta": {"is_kfs": True, "loan_category": "retail", "is_digital_loan": False,
                     "sanction_date": "2024-10-15", "language": "en"},
        "lender": {"name": "Test Bank", "type": "bank"},
        "proposal_no": ev("KFS-001"),
        "loan": {
            "sanctioned_amount_inr": ev(20000.0),
            "disbursal": "upfront",
            "term_months": ev(24),
            "instalments": [{"type": "EMI",
                              "count": ev(24),
                              "amount_inr": ev(970.0),
                              "first_due_after_days": ev(30)}],
        },
        "rate": {"interest_rate_pct": ev(15.0), "type": "fixed", "floating": None},
        "charges": [
            {"name": "Processing fee", "category": "processing", "payee": "lender",
             "frequency": "one_time", "recurrence": None,
             "amount_inr": 240.0, "percent": None, "percent_of": None,
             "gst_included": None, "page": 1, "quote": "Processing fee Rs. 240"},
            {"name": "Third-party fee", "category": "legal", "payee": "third_party",
             "frequency": "one_time", "recurrence": None,
             "amount_inr": 160.0, "percent": None, "percent_of": None,
             "gst_included": None, "page": 1, "quote": "Third-party fee Rs. 160"},
        ],
        "stated_apr_pct": ev(17.07),
        "contingent_charges": [
            {"name": "Penal charges", "value": "2% p.m.", "page": 2},
            {"name": "Foreclosure charges", "value": "Nil", "page": 2},
            {"name": "Switching charges", "value": "Nil", "page": 2},
        ],
        "validity_period": {"value": 3, "unit": "working_days", "page": 1},
        "cooling_off_days": ev_none(),
        "grievance": {"officer_name": "John Doe", "phone": "1800-123-4567",
                      "email": "nodal@testbank.com", "page": 2},
        "recovery_agent_clause_ref": ev_none(),
        "flags_present": {"apr_computation_sheet": True, "amortisation_schedule": True},
        "amortisation_schedule": [],
    }


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _mock_scope_in_scope():
    from app.services.extractor import ClassifierResult
    return ClassifierResult(
        is_kfs=True, loan_category="retail", is_digital_loan=False,
        confidence=0.98, reason="Clearly a KFS.", in_scope=True,
    )


def _mock_scope_out_of_scope():
    from app.services.extractor import ClassifierResult
    return ClassifierResult(
        is_kfs=False, loan_category="other", is_digital_loan=None,
        confidence=0.95, reason="This is a salary slip.", in_scope=False,
    )


def _mock_extraction_success():
    from app.services.extractor import ExtractionResult
    from app.models.schemas import KFSExtraction
    return ExtractionResult(
        success=True,
        extraction=KFSExtraction(**_minimal_extraction_dict()),
        not_found_critical=[],
        validation_errors=[],
        retry_attempted=False,
    )


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_stores():
    """Reset all in-memory stores before each test to ensure isolation."""
    _storage._UPLOADS.clear()
    _storage._EXTRACTIONS.clear()
    _storage._ANALYSES.clear()
    yield


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------

class TestHealthz:
    def test_returns_200(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_response_contains_cloud_run_fields(self, client):
        """Health check must expose the fields Cloud Run operators care about."""
        r = client.get("/healthz")
        body = r.json()
        assert "gemini_model"     in body, "gemini_model missing from /healthz"
        assert "storage_backend"  in body, "storage_backend missing from /healthz"
        assert "gcp_project"      in body, "gcp_project missing from /healthz"
        assert "location"         in body, "location missing from /healthz"

    def test_content_type_is_json(self, client):
        r = client.get("/healthz")
        assert "application/json" in r.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------

class TestUploadValidation:
    def test_pdf_accepted(self, client):
        r = client.post("/api/v1/documents",
                        files={"file": ("test.pdf", _make_pdf(), "application/pdf")})
        assert r.status_code == 201
        assert "doc_id" in r.json()

    def test_jpeg_accepted(self, client):
        r = client.post("/api/v1/documents",
                        files={"file": ("photo.jpg", _make_jpeg(), "image/jpeg")})
        assert r.status_code == 201

    def test_png_accepted(self, client):
        r = client.post("/api/v1/documents",
                        files={"file": ("scan.png", _make_png(), "image/png")})
        assert r.status_code == 201

    def test_bad_type_rejected(self, client):
        """A plain-text file (not PDF/JPEG/PNG) must be rejected with 415."""
        r = client.post("/api/v1/documents",
                        files={"file": ("resume.txt", b"hello world this is a resume", "text/plain")})
        assert r.status_code == 415
        assert "not supported" in r.json()["detail"].lower()

    def test_docx_rejected(self, client):
        """A docx (ZIP) must be rejected with 415."""
        # Minimal ZIP/DOCX magic bytes
        docx = b"PK\x03\x04" + b"\x00" * 26
        r = client.post("/api/v1/documents",
                        files={"file": ("kfs.docx", docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        assert r.status_code == 415

    def test_oversize_rejected(self, client):
        """A file > 10 MB must be rejected with 413."""
        big = b"%PDF-1.4 " + b"A" * (10 * 1024 * 1024 + 1)
        r = client.post("/api/v1/documents",
                        files={"file": ("big.pdf", big, "application/pdf")})
        assert r.status_code == 413
        assert "10 MB" in r.json()["detail"]

    def test_upload_returns_request_id_header(self, client):
        r = client.post("/api/v1/documents",
                        files={"file": ("test.pdf", _make_pdf(), "application/pdf")})
        assert "X-Request-ID" in r.headers


# ---------------------------------------------------------------------------
# Full flow: upload → extract → patch → analyze → get
# ---------------------------------------------------------------------------

class TestFullFlow:
    def _upload(self, client) -> str:
        r = client.post("/api/v1/documents",
                        files={"file": ("kfs.pdf", _make_pdf(), "application/pdf")})
        assert r.status_code == 201
        return r.json()["doc_id"]

    def _extract(self, client, doc_id) -> str:
        with patch("app.routers.documents.scope_guard", return_value=_mock_scope_in_scope()), \
             patch("app.routers.documents.extract", return_value=_mock_extraction_success()):
            r = client.post(f"/api/v1/documents/{doc_id}/extract")
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True
        return r.json()["extraction_id"]

    def test_upload_extract_patch_analyze_get(self, client):
        # 1. Upload
        doc_id = self._upload(client)

        # 2. Extract
        extraction_id = self._extract(client, doc_id)

        # 3. Patch
        r = client.patch(
            f"/api/v1/extractions/{extraction_id}",
            json={"fields": {"user_note": "manually verified"}},
        )
        assert r.status_code == 200
        assert r.json()["user_note"] == "manually verified"

        # 4. Analyze
        r = client.post(f"/api/v1/extractions/{extraction_id}/analyze")
        assert r.status_code == 201, r.text
        body = r.json()
        assert "analysis_id" in body
        assert "rule_results" in body
        analysis_id = body["analysis_id"]

        # 5. Get analysis
        r = client.get(f"/api/v1/analyses/{analysis_id}")
        assert r.status_code == 200
        assert r.json()["analysis_id"] == analysis_id

    def test_extract_missing_doc_returns_404(self, client):
        r = client.post("/api/v1/documents/nonexistent-id/extract")
        assert r.status_code == 404

    def test_analyze_missing_extraction_returns_404(self, client):
        r = client.post("/api/v1/extractions/nonexistent-id/analyze")
        assert r.status_code == 404

    def test_get_missing_analysis_returns_404(self, client):
        r = client.get("/api/v1/analyses/nonexistent-id")
        assert r.status_code == 404

    def test_out_of_scope_document_returns_422(self, client):
        doc_id = self._upload(client)
        with patch("app.routers.documents.scope_guard", return_value=_mock_scope_out_of_scope()):
            r = client.post(f"/api/v1/documents/{doc_id}/extract")
        assert r.status_code == 422
        assert r.json()["in_scope"] is False

    def test_patch_missing_extraction_returns_404(self, client):
        r = client.patch("/api/v1/extractions/bad-id", json={"fields": {"x": 1}})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------

class TestSamples:
    def test_list_samples(self, client):
        r = client.get("/api/v1/samples")
        assert r.status_code == 200
        keys = {s["key"] for s in r.json()["samples"]}
        assert "rbi_annex_b" in keys
        assert "synthetic_bad_kfs" in keys

    def test_load_rbi_annex_b_without_gemini(self, client):
        """Loading a sample must NOT call Gemini at all."""
        # Patch at the gemini_client module level — that's where the real call lives
        with patch("app.services.gemini_client.Client") as mock_client:
            r = client.post("/api/v1/samples/rbi_annex_b/load")
        mock_client.assert_not_called()
        assert r.status_code == 200
        body = r.json()
        assert body["sample_key"] == "rbi_annex_b"
        assert body["synthetic"] is False
        assert "extraction_id" in body
        assert "analysis_id" in body

    def test_load_synthetic_bad_kfs_is_marked_synthetic(self, client):
        r = client.post("/api/v1/samples/synthetic_bad_kfs/load")
        assert r.status_code == 200
        assert r.json()["synthetic"] is True

    def test_load_placeholder_returns_503(self, client):
        r = client.post("/api/v1/samples/real_lender_kfs/load")
        assert r.status_code == 503

    def test_load_unknown_sample_returns_404(self, client):
        r = client.post("/api/v1/samples/does_not_exist/load")
        assert r.status_code == 404

    def test_sample_analysis_retrievable_by_id(self, client):
        r = client.post("/api/v1/samples/rbi_annex_b/load")
        assert r.status_code == 200
        analysis_id = r.json()["analysis_id"]

        r2 = client.get(f"/api/v1/analyses/{analysis_id}")
        assert r2.status_code == 200
        assert r2.json()["analysis_id"] == analysis_id

    def test_demo_query_param_accepted(self, client):
        r = client.post("/api/v1/samples/rbi_annex_b/load?demo=1")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# PII redaction
# ---------------------------------------------------------------------------

class TestPIIRedaction:
    def test_pii_absent_from_stored_analysis(self, client):
        """The analysis record in the store must not contain lender name, officer name,
        phone, or email from the extraction."""
        # Upload + extract
        doc_id_r = client.post("/api/v1/documents",
                               files={"file": ("kfs.pdf", _make_pdf(), "application/pdf")})
        doc_id = doc_id_r.json()["doc_id"]

        with patch("app.routers.documents.scope_guard", return_value=_mock_scope_in_scope()), \
             patch("app.routers.documents.extract", return_value=_mock_extraction_success()):
            ext_r = client.post(f"/api/v1/documents/{doc_id}/extract")
        extraction_id = ext_r.json()["extraction_id"]

        # Analyze
        ana_r = client.post(f"/api/v1/extractions/{extraction_id}/analyze")
        assert ana_r.status_code == 201
        analysis_id = ana_r.json()["analysis_id"]

        # Retrieve stored record directly from in-memory store
        stored = _storage.get_analysis(analysis_id)
        assert stored is not None

        # Serialize to string and check for PII values from the fixture
        stored_str = json.dumps(stored)
        pii_values = ["John Doe", "1800-123-4567", "nodal@testbank.com", "Test Bank"]
        for pii in pii_values:
            assert pii not in stored_str, (
                f"PII value '{pii}' found in stored analysis record. "
                f"Redaction is not working correctly."
            )

    def test_non_pii_fields_preserved_after_redaction(self, client):
        """Non-PII fields (rule IDs, APR values, loan category) must survive redaction."""
        doc_id_r = client.post("/api/v1/documents",
                               files={"file": ("kfs.pdf", _make_pdf(), "application/pdf")})
        doc_id = doc_id_r.json()["doc_id"]

        with patch("app.routers.documents.scope_guard", return_value=_mock_scope_in_scope()), \
             patch("app.routers.documents.extract", return_value=_mock_extraction_success()):
            ext_r = client.post(f"/api/v1/documents/{doc_id}/extract")
        extraction_id = ext_r.json()["extraction_id"]

        ana_r = client.post(f"/api/v1/extractions/{extraction_id}/analyze")
        analysis_id = ana_r.json()["analysis_id"]

        stored = _storage.get_analysis(analysis_id)
        stored_str = json.dumps(stored)
        # Rule IDs and APR values are not PII and must survive
        assert "R-01" in stored_str, "Rule ID R-01 should survive redaction"
        assert "analysis_id" in stored_str
        # APR numeric value present
        assert "apr" in stored_str or "all_charges" in stored_str


# ---------------------------------------------------------------------------
# Analyze requires successful extraction
# ---------------------------------------------------------------------------

class TestAnalyzeGuards:
    def test_analyze_out_of_scope_extraction_returns_422(self, client):
        """Trying to analyze an out-of-scope extraction record should return 422."""
        # Store a failed record manually
        eid = "test-oos-extraction"
        _storage.store_extraction(eid, {
            "extraction_id": eid,
            "doc_id": "d1",
            "in_scope": False,
            "success": False,
            "extraction": None,
        })
        r = client.post(f"/api/v1/extractions/{eid}/analyze")
        assert r.status_code == 422
