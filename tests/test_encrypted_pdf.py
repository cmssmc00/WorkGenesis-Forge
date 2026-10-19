"""Encrypted PDFs are rejected as unsuitable sources instead of crashing acquisition."""
import tempfile
import unittest
from pathlib import Path

try:
    import cryptography  # noqa: F401  (pypdf needs it for AES)
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

from pypdf import PdfWriter

from wgforge.artifacts import extract_artifact


@unittest.skipUnless(HAS_CRYPTO, "cryptography is a declared dependency; install it to run this check")
class EncryptedPdfTests(unittest.TestCase):
    def test_aes256_permission_only_pdf_is_rejected_as_value_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "signed_report.pdf"
            writer = PdfWriter()
            writer.add_blank_page(200, 200)
            writer.encrypt(user_password="", owner_password="owner", algorithm="AES-256")
            with open(path, "wb") as handle:
                writer.write(handle)
            with self.assertRaisesRegex(ValueError, "Encrypted"):
                extract_artifact(path)


if __name__ == "__main__":
    unittest.main()
