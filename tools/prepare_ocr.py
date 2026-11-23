"""Stage pinned browser OCR assets. Run at build/preview time, not at runtime.

No npm install or package scripts are needed. Each archive is verified before
individual known members are copied. The running browser requests assets
only from the same origin. The Python CLI stays dependency-free.
"""
import argparse
import base64
import hashlib
import io
import tarfile
import urllib.request
from pathlib import Path

ASSETS = (
    (
        "tesseract.js/-/tesseract.js-6.0.1.tgz",
        "/sPvMvrCtgxnNRCjbTYbr7BRu0yfWDsMZQ2a/T5aN/L1t8wUQN6tTWv6p6FwzpoEBA0jrN2UD2SX4QQFRdoDbA==",
        {
            "dist/tesseract.min.js": "tesseract.min.js",
            "dist/tesseract.min.js.LICENSE.txt": "tesseract.min.js.LICENSE.txt",
            "dist/worker.min.js": "worker.min.js",
            "dist/worker.min.js.LICENSE.txt": "worker.min.js.LICENSE.txt",
            "LICENSE.md": "tesseract.js.LICENSE.md",
        },
    ),
    (
        "tesseract.js-core/-/tesseract.js-core-6.0.0.tgz",
        "1Qncm/9oKM7xgrQXZXNB+NRh19qiXGhxlrR8EwFbK5SaUbPZnS5OMtP/ghtqfd23hsr1ZvZbZjeuAGcMxd/ooA==",
        {
            "tesseract-core-lstm.wasm.js": "tesseract-core-lstm.wasm.js",
            "LICENSE": "tesseract-core.LICENSE",
        },
    ),
    (
        "@tesseract.js-data/eng/-/eng-1.0.0.tgz",
        "mbTumm6KQPUHyzTPQaF3ObXYnx0SqqfV2nabqFVQBwD6Kl7PhGSLSzOlfFTWy0P3BjghaSKA2W9GB19Jk+ZcTg==",
        {"4.0.0_best_int/eng.traineddata.gz": "eng.traineddata.gz", "README.md": "eng.README.md"},
    ),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("web/vendor/ocr"))
    parser.add_argument("--archives", type=Path, help="optional directory with the three named npm tarballs")
    args = parser.parse_args()

    files = {}
    for url, integrity, members in ASSETS:
        if args.archives:
            data = (args.archives / url.rsplit("/", 1)[1]).read_bytes()
        else:
            with urllib.request.urlopen("https://registry.npmjs.org/" + url, timeout=90) as response:
                data = response.read(32 * 1024 * 1024)
        actual = base64.b64encode(hashlib.sha512(data).digest()).decode()
        if actual != integrity:
            raise SystemExit("SHA-512 mismatch for " + url)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member, name in members.items():
                entry = archive.getmember("package/" + member)
                if not entry.isfile():
                    raise SystemExit("Unexpected archive entry: " + member)
                with archive.extractfile(entry) as obj:
                    files[name] = obj.read()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (args.out / name).write_bytes(data)
    print("Verified OCR assets staged in " + str(args.out))


if __name__ == "__main__":
    main()
