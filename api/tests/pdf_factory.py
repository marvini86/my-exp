"""Builds minimal, valid PDFs for tests (no dependencies)."""


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(lines: list[str] | None = None) -> bytes:
    """1-page PDF. With `lines` it has a text layer; without, it simulates a scanned one."""
    if lines:
        commands = ["BT", "/F1 11 Tf", "14 TL", "50 800 Td"]
        for line in lines:
            commands.append(f"({_escape(line)}) Tj T*")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1")
    else:
        stream = b""

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


ENERGY_BILL_LINES = [
    "COMPANHIA ENERGETICA EXEMPLO S.A.  CNPJ 33.050.071/0001-58",
    "CONTA DE ENERGIA ELETRICA - NOTA FISCAL/FATURA",
    "Cliente: Ana Souza   Instalacao: 1234567890",
    "Mes de referencia: JAN/2024   Emissao: 15/01/2024   Vencimento: 05/02/2024",
    "Leitura anterior: 10450   Leitura atual: 10712   Consumo: 262 kWh",
    "Energia eletrica consumo ................ R$ 198,45",
    "Bandeira tarifaria amarela ............. R$ 7,30",
    "Contribuicao iluminacao publica ........ R$ 12,05",
    "TOTAL A PAGAR: R$ 217,80",
    "Linha digitavel: 83650000002-1 17800138001-2 23456789012-3 40520240205-4",
]
