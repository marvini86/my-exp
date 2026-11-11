from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from models.financial import PaymentStatus
from schemas.extraction import DocumentType
from services.document_processor import PermanentProcessingError
from services.extraction.nfe_xml import NFeXmlExtractor

NFCE = (Path(__file__).parent / "fixtures" / "nfce_supermercado.xml").read_bytes()

extractor = NFeXmlExtractor()


def nfe_55(payment_code: str) -> bytes:
    """NF-e model 55, without nfeProc, with a configurable payment method."""
    return f"""<?xml version="1.0"?>
<NFe xmlns="http://www.portalfiscal.inf.br/nfe"><infNFe Id="NFe999">
  <ide><mod>55</mod><nNF>77</nNF><dhEmi>2024-02-01T09:00:00-03:00</dhEmi></ide>
  <emit><CNPJ>11222333000144</CNPJ><xNome>LOJA DE MOVEIS SA</xNome></emit>
  <det nItem="1"><prod><cProd>X</cProd><cEAN></cEAN><xProd>MESA</xProd><uCom>UN</uCom>
    <qCom>1</qCom><vUnCom>900.00</vUnCom><vProd>900.00</vProd></prod></det>
  <total><ICMSTot><vNF>900.00</vNF></ICMSTot></total>
  <pag><detPag><tPag>{payment_code}</tPag></detPag></pag>
</infNFe></NFe>""".encode()


class TestNfce:
    def test_header(self):
        doc = extractor.extract(NFCE)

        assert doc.document_type is DocumentType.NFCE
        assert doc.establishment == "Mercado Exemplo"
        assert doc.establishment_tax_id == "12345678000190"
        assert doc.issued_at == date(2024, 1, 10)
        assert doc.total_amount == Decimal("63.43")
        assert doc.payment_status is PaymentStatus.PAID
        assert doc.paid_at.isoformat() == "2024-01-10T18:32:15-03:00"
        assert doc.description == "Mercado Exemplo - NFC-e 12345"

    def test_metadata(self):
        meta = extractor.extract(NFCE).metadata

        assert meta["access_key"] == "35240112345678000190650010000123451000123456"
        assert meta["authorization_status"] == "100"
        assert meta["payment_methods"] == ["pix"]
        assert meta["discount"] == "2.00"

    def test_items(self):
        milk, rice, bread = extractor.extract(NFCE).items

        assert (milk.description, milk.sku, milk.quantity, milk.unit_price, milk.total_price) == (
            "LEITE INTEGRAL 1L",
            "7891000100103",
            Decimal("6.000"),
            Decimal("4.8900"),
            Decimal("29.34"),
        )
        # Item discount: net total, gross kept in metadata.
        assert rice.total_price == Decimal("25.90")
        assert rice.metadata["gross_total"] == "27.90"
        assert rice.metadata["discount"] == "2.00"
        # Bulk item and "SEM GTIN".
        assert (bread.sku, bread.unit, bread.quantity) == (None, "KG", Decimal("0.512"))

    def test_item_totals_match_document_total(self):
        doc = extractor.extract(NFCE)

        assert sum(item.total_price for item in doc.items) == doc.total_amount


class TestNfe55:
    def test_card_payment_is_paid(self):
        doc = extractor.extract(nfe_55("03"))

        assert doc.document_type is DocumentType.NFE
        assert doc.establishment == "LOJA DE MOVEIS SA"  # no xFant → xNome
        assert doc.payment_status is PaymentStatus.PAID
        assert doc.items[0].sku is None

    def test_bank_slip_payment_status_is_unknown(self):
        doc = extractor.extract(nfe_55("15"))

        assert doc.payment_status is PaymentStatus.UNKNOWN
        assert doc.paid_at is None


class TestRejections:
    @pytest.mark.parametrize(
        "payload",
        [
            # Billion laughs / entities
            b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><NFe>&lol;</NFe>',
            # XXE
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><NFe>&xxe;</NFe>',
        ],
    )
    def test_rejects_dtd_and_entities(self, payload):
        with pytest.raises(PermanentProcessingError, match="unsafe"):
            extractor.extract(payload)

    def test_rejects_malformed_xml(self):
        with pytest.raises(PermanentProcessingError, match="Invalid"):
            extractor.extract(b"<NFe><unclosed></NFe>")

    def test_rejects_xml_that_is_not_nfe(self):
        with pytest.raises(PermanentProcessingError, match="not an NF-e"):
            extractor.extract(b'<?xml version="1.0"?><invoice><total>10</total></invoice>')

    def test_rejects_invalid_number(self):
        with pytest.raises(PermanentProcessingError, match="Invalid number"):
            extractor.extract(nfe_55("03").replace(b"<vNF>900.00</vNF>", b"<vNF>abc</vNF>"))
