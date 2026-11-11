"""Deterministic reading of NF-e (model 55) and NFC-e (model 65) XML.

No AI: the XML is the official, structured source, with items, values and issuer.
`defusedxml` blocks DTDs/entities (billion laughs, XXE).
"""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from models.financial import PaymentStatus
from schemas.extraction import DocumentType, ExtractedDocument, ExtractedItem
from services.document_processor import PermanentProcessingError

NFE_NAMESPACE = "http://www.portalfiscal.inf.br/nfe"
_NS = {"n": NFE_NAMESPACE}

_CENTS = Decimal("0.01")
_QUANTITY_PLACES = Decimal("0.001")
_UNIT_PRICE_PLACES = Decimal("0.0001")

# Most common tPag (payment method) codes.
_PAYMENT_METHODS = {
    "01": "cash",
    "02": "check",
    "03": "credit_card",
    "04": "debit_card",
    "05": "store_credit",
    "10": "meal_voucher",
    "11": "food_voucher",
    "15": "bank_slip",
    "16": "bank_deposit",
    "17": "pix",
    "90": "no_payment",
    "99": "other",
}


class NFeXmlExtractor:
    def supports(self, mime_type: str) -> bool:
        return mime_type == "application/xml"

    def extract(self, content: bytes, mime_type: str = "application/xml") -> ExtractedDocument:
        root = self._parse(content)

        inf = root if self._local_name(root) == "infNFe" else root.find(".//n:infNFe", _NS)
        if inf is None:
            raise PermanentProcessingError("Unsupported XML: not an NF-e/NFC-e document")

        model = self._text(inf, "n:ide/n:mod")
        number = self._text(inf, "n:ide/n:nNF")
        issued = self._parse_datetime(self._text(inf, "n:ide/n:dhEmi") or self._text(inf, "n:ide/n:dEmi"))

        establishment = self._text(inf, "n:emit/n:xFant") or self._text(inf, "n:emit/n:xNome")
        tax_id = self._text(inf, "n:emit/n:CNPJ") or self._text(inf, "n:emit/n:CPF")

        is_nfce = model == "65"
        document_type = DocumentType.NFCE if is_nfce else DocumentType.NFE
        label = "NFC-e" if is_nfce else "NF-e"

        payment_methods = [
            _PAYMENT_METHODS.get(code, code)
            for code in (el.text for el in inf.findall("n:pag/n:detPag/n:tPag", _NS))
            if code
        ]

        # An NFC-e is issued at purchase time, already paid. An NF-e may be invoiced (bank slip).
        paid_now = is_nfce or (payment_methods and not {"bank_slip", "no_payment"} & set(payment_methods))

        access_key = (inf.get("Id") or "").removeprefix("NFe") or None
        authorization_status = self._text(root, ".//n:protNFe/n:infProt/n:cStat")

        return ExtractedDocument(
            document_type=document_type,
            description=f"{establishment or 'Compra'} - {label} {number or ''}".strip(),
            establishment=establishment,
            establishment_tax_id=tax_id,
            issued_at=issued.date() if issued else None,
            paid_at=issued if paid_now else None,
            payment_status=PaymentStatus.PAID if paid_now else PaymentStatus.UNKNOWN,
            total_amount=self._money(self._text(inf, "n:total/n:ICMSTot/n:vNF")),
            items=[self._item(det) for det in inf.findall("n:det", _NS)],
            metadata={
                "source": "nfe_xml",
                # Same convention as the AI extractor: legal name here, trade name in `establishment`.
                "legal_name": self._text(inf, "n:emit/n:xNome"),
                "model": model,
                "number": number,
                "series": self._text(inf, "n:ide/n:serie"),
                "access_key": access_key,
                "authorization_status": authorization_status,
                "payment_methods": payment_methods,
                "discount": self._str_decimal(self._money(self._text(inf, "n:total/n:ICMSTot/n:vDesc"))),
            },
        )

    def _item(self, det: Element) -> ExtractedItem:
        prod = det.find("n:prod", _NS)
        if prod is None:
            raise PermanentProcessingError("Invalid NF-e: item without <prod>")

        gross = self._money(self._text(prod, "n:vProd"))
        discount = self._money(self._text(prod, "n:vDesc")) or Decimal("0")
        net = (gross - discount).quantize(_CENTS) if gross is not None else None

        ean = self._text(prod, "n:cEAN")
        sku = ean if ean and ean.isdigit() else None  # "SEM GTIN" → None

        return ExtractedItem(
            description=self._text(prod, "n:xProd") or "Item",
            sku=sku,
            quantity=self._decimal(self._text(prod, "n:qCom"), _QUANTITY_PLACES),
            unit=self._text(prod, "n:uCom"),
            unit_price=self._decimal(self._text(prod, "n:vUnCom"), _UNIT_PRICE_PLACES),
            total_price=net if net is not None and net >= 0 else gross,
            metadata={
                "item_number": det.get("nItem"),
                "product_code": self._text(prod, "n:cProd"),
                "ncm": self._text(prod, "n:NCM"),
                "gross_total": self._str_decimal(gross),
                "discount": self._str_decimal(discount) if discount else None,
            },
        )

    @staticmethod
    def _parse(content: bytes) -> Element:
        try:
            return fromstring(content, forbid_dtd=True)
        except (ParseError, DefusedXmlException) as exc:
            raise PermanentProcessingError(f"Invalid or unsafe XML: {type(exc).__name__}") from exc

    @staticmethod
    def _local_name(element: Element) -> str:
        return element.tag.rsplit("}", 1)[-1]

    @staticmethod
    def _text(element: Element, path: str) -> str | None:
        found = element.find(path, _NS)
        if found is None or found.text is None:
            return None
        return found.text.strip() or None

    @classmethod
    def _money(cls, value: str | None) -> Decimal | None:
        return cls._decimal(value, _CENTS)

    @staticmethod
    def _decimal(value: str | None, places: Decimal) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(value).quantize(places, rounding=ROUND_HALF_UP)
        except InvalidOperation as exc:
            raise PermanentProcessingError(f"Invalid number in NF-e: {value!r}") from exc

    @staticmethod
    def _str_decimal(value: Decimal | None) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if value is None:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise PermanentProcessingError(f"Invalid date in NF-e: {value!r}") from exc
