"""Photo reading evaluation: the real photos in degraded versions, read by the real vision AI.

1) Download the photos and the verified reading (through the local API, with the development login):
       .venv/Scripts/python.exe evals/photos/run.py fetch --email you@example.com
   Saved to evals/data/photos/ (outside Git: they are personal documents). CHECK the readings
   in the app first: they become the reference ("expected.json").

2) Run:
       .venv/Scripts/python.exe evals/photos/run.py run [--variants original,blurred] [--limit 3] [--pause 2]
   Uses 1+ vision call per photo and version (the quality gate may try another model).
   Result in evals/results/photos-YYYYMMDD-HHMM.json.
"""

import argparse
import io
import json
import sys
import time
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(API_DIR / "app")]

DATA_DIR = API_DIR / "evals" / "data" / "photos"
EXPECTED = DATA_DIR / "expected.json"
RESULTS_DIR = API_DIR / "evals" / "results"
TOLERANCE = Decimal("0.05")


# --- degraded versions of the photos -------------------------------------------------------------


def _variants() -> dict[str, Callable]:
    from PIL import Image, ImageEnhance, ImageFilter

    def jpeg(image: Image.Image, quality: int = 85) -> bytes:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "JPEG", quality=quality)
        return buffer.getvalue()

    def small(image):  # forwarded several times through WhatsApp
        copy = image.copy()
        copy.thumbnail((900, 900))
        return jpeg(copy, quality=35)

    return {
        "original": lambda image: jpeg(image, quality=92),
        "small": small,
        "rotated": lambda image: jpeg(image.rotate(90, expand=True)),
        "tilted": lambda image: jpeg(image.rotate(7, expand=True, fillcolor="white")),
        "dark": lambda image: jpeg(ImageEnhance.Contrast(ImageEnhance.Brightness(image).enhance(0.45)).enhance(0.8)),
        "blurred": lambda image: jpeg(image.filter(ImageFilter.GaussianBlur(1.6))),
    }


# --- fetch --------------------------------------------------------------------------------


def fetch(email: str, api_url: str) -> None:
    import httpx

    client = httpx.Client(base_url=api_url, timeout=60)
    login = client.post("/auth/dev-login", json={"email": email})
    if login.status_code != 200:
        sys.exit(f"Development login failed ({login.status_code}): is the API running in development?")
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    expected: dict[str, dict] = {}
    for account in client.get("/accounts/me").json():
        base = f"/accounts/{account['id']}/documents"
        page = client.get(f"{base}/", params={"status": "completed", "limit": 100}).json()
        for doc in page["items"]:
            if not (doc["mime_type"] or "").startswith("image/"):
                continue
            detail = client.get(f"{base}/{doc['id']}").json()
            if not detail["entries"]:
                continue
            url = client.get(f"{base}/{doc['id']}/download").json()["url"]
            name = f"{doc['id']}.jpg"
            (DATA_DIR / name).write_bytes(httpx.get(url, timeout=60).content)
            entry = detail["entries"][0]
            items = detail["items"]
            expected[name] = {
                "document_type": detail["document_type"],
                "total": entry["amount"],
                "items": len(items),
                "items_sum": str(sum(Decimal(i["total_price"] or "0") for i in items)),
                "establishment": entry.get("establishment"),
                "occurred_at": entry.get("occurred_at"),
            }
            print(f"  {name}  {detail['document_type']:12} R$ {entry['amount']:>8}  {len(items):2} items  {entry.get('establishment')}")
    EXPECTED.write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(expected)} photos in {DATA_DIR}\nCheck the readings above in the app: they are the evaluation reference.")


# --- run ----------------------------------------------------------------------------------


def compare(expected: dict, document) -> list[str]:
    problems = []
    if document.document_type.value != expected["document_type"]:
        problems.append(f"type {document.document_type.value}")
    if document.total_amount is None or abs(document.total_amount - Decimal(expected["total"])) > Decimal("0.001"):
        problems.append(f"total {document.total_amount}")
    if len(document.items) != expected["items"]:
        problems.append(f"{len(document.items)}/{expected['items']} items")
    elif expected["items"]:
        got = sum((i.total_price or Decimal("0")) for i in document.items)
        if abs(got - Decimal(expected["items_sum"])) > TOLERANCE:
            problems.append(f"items sum {got}")
    return problems


def run(variant_names: list[str] | None, limit: int | None, pause: float) -> None:
    from PIL import Image

    from core.config import settings
    from services.document_processor import PermanentProcessingError
    from services.extraction.llm import LLMDocumentExtractor
    from services.llm_router import LLMUnavailableError

    if not EXPECTED.exists():
        sys.exit("Run first: evals/photos/run.py fetch --email ...")
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    variants = _variants()
    names = variant_names or list(variants)
    photos = sorted(expected)[:limit] if limit else sorted(expected)
    extractor = LLMDocumentExtractor.for_settings(settings)

    print(f"{len(photos)} photos × {len(names)} versions = {len(photos) * len(names)} readings\n")
    results = []
    for photo in photos:
        image = Image.open(DATA_DIR / photo)
        image.load()
        for name in names:
            content = variants[name](image)
            started = time.monotonic()
            try:
                document = extractor.extract(content, "image/jpeg")
                problems = compare(expected[photo], document)
                model = document.metadata.get("model")
                tried = document.metadata.get("models_tried")
            except LLMUnavailableError as exc:
                print(f"Vision AI unavailable ({exc}); stopping.")
                return _report(results)
            except PermanentProcessingError as exc:
                problems, model, tried = [f"unreadable: {exc}"], None, None
            seconds = time.monotonic() - started
            results.append({"photo": photo, "variant": name, "problems": problems, "model": model,
                            "models_tried": tried, "seconds": round(seconds, 1)})
            mark = "OK " if not problems else "ERR"
            print(f"[{mark}] {photo[:8]} {name:9} {seconds:5.1f}s {model or '':34} {'; '.join(problems)}")
            time.sleep(pause)
    _report(results)


def _report(results: list[dict]) -> None:
    if not results:
        return
    print("\nBy version:")
    for name in dict.fromkeys(r["variant"] for r in results):
        rows = [r for r in results if r["variant"] == name]
        ok = sum(not r["problems"] for r in rows)
        avg = sum(r["seconds"] for r in rows) / len(rows)
        print(f"  {name:9} {ok}/{len(rows)} correct  ({avg:.0f}s on average)")
    total_ok = sum(not r["problems"] for r in results)
    print(f"Total: {total_ok}/{len(results)}")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"photos-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Detalhes: {out}")


def main() -> None:
    import logging

    logging.basicConfig(level=logging.WARNING)
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Photo reading evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--email", required=True)
    f.add_argument("--api", default="http://127.0.0.1:8000")
    r = sub.add_parser("run")
    r.add_argument("--variants", help="e.g. original,blurred (default: all)")
    r.add_argument("--limit", type=int, help="only the first N photos")
    r.add_argument("--pause", type=float, default=2.0)
    args = parser.parse_args()
    if args.command == "fetch":
        fetch(args.email, args.api)
    else:
        run(args.variants.split(",") if args.variants else None, args.limit, args.pause)


if __name__ == "__main__":
    main()
