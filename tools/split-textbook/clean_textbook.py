"""구매한 교재 PDF에서 구매자 워터마크를 지워 textbook_original/에 넣는다.

다산에듀 교재 PDF는 쪽마다 두 가지가 찍혀 있다.
- 가운데 대각선 워터마크: 콘텐츠 스트림 안의 45° 회전 텍스트 블록 하나
  (`q 0.707107 -0.707107 0.707107 0.707107 x y cm BT … Tj T* ET Q`).
- 맨 위 안내 문구: 쪽 위쪽 끝(y < 30pt)에 있는, "응원합니다"가 든 한 줄.

둘 다 구매자 개인정보가 들어 있으므로, 이 스크립트에는 이름·이메일·회원번호를 적지 않고
위치와 구조만으로 찾는다. 로그에도 찾은 글자를 출력하지 않는다 (개수와 쪽 번호만).
"""

import argparse
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent.parent

# 가운데 대각선 워터마크 블록. 좌표, 글자 시작 위치, 문자열 내용은 어떤 값이든 받는다.
WATERMARK_RE = re.compile(
    rb"q\s*0\.707107\s+-0\.707107\s+0\.707107\s+0\.707107\s+[0-9.]+\s+[0-9.]+\s+cm\s*"
    rb"BT\s*1 0 0 1 -?[0-9.]+ 0 Tm\s*"
    rb"\((?:[^()\\]|\\.)*\)\s*Tj\s*T\*\s*ET\s*Q"
)
BANNER_MAX_Y = 30  # 안내 문구 줄의 아래 끝이 이보다 위에 있다 [pt]
BANNER_MARK = "응원합니다"


def banner_lines(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """쪽 맨 위의 안내 문구 줄들의 영역. 글자는 개인정보라 돌려주지 않는다."""
    found = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"])
            if line["bbox"][3] < BANNER_MAX_Y and BANNER_MARK in text:
                found.append(pymupdf.Rect(line["bbox"]))
    return found


def clean(doc: pymupdf.Document) -> tuple[list[int], list[int]]:
    """워터마크와 안내 문구를 지운다. 둘이 각각 정확히 하나가 아닌 쪽 번호(1부터)를 돌려준다."""
    bad_watermark, bad_banner = [], []
    for page in doc:
        count = 0
        for xref in page.get_contents():
            stream, n = WATERMARK_RE.subn(b"", doc.xref_stream(xref))
            if n:
                doc.update_stream(xref, stream)
                count += n
        if count != 1:
            bad_watermark.append(page.number + 1)

        # 줄 영역을 통째로 지운다. 줄 글자로 search_for를 하면 일부 쪽에서 문구가 남는다.
        rects = banner_lines(page)
        if len(rects) != 1:
            bad_banner.append(page.number + 1)
        for rect in rects:
            page.add_redact_annot(rect, fill=False, cross_out=False)
        if rects:
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
    return bad_watermark, bad_banner


def leftovers(path: Path) -> int:
    """저장한 파일에 남은 워터마크 블록과 안내 문구 줄 수."""
    with pymupdf.open(path) as doc:
        return sum(
            len(WATERMARK_RE.findall(doc.xref_stream(xref)))
            for page in doc
            for xref in page.get_contents()
        ) + sum(len(banner_lines(page)) for page in doc)


def pages_text(pages: list[int]) -> str:
    return ", ".join(map(str, pages[:10])) + (" …" if len(pages) > 10 else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("inputs", nargs="+", type=Path, help="구매한 교재 PDF (여러 개 가능)")
    parser.add_argument("--out", type=Path, default=ROOT / "textbook_original", help="결과 폴더")
    parser.add_argument("--force", action="store_true", help="결과 파일이 이미 있어도 덮어쓴다")
    args = parser.parse_args()

    failed = False
    for src in args.inputs:
        src = src.expanduser().resolve()
        dst = (args.out / src.name).resolve()
        print(f"{src.name}")
        if not src.is_file():
            print("  오류: 파일이 없다", file=sys.stderr)
            failed = True
            continue
        if src == dst:
            print("  오류: 입력과 출력이 같은 파일이다 (원본 보호)", file=sys.stderr)
            failed = True
            continue
        if dst.exists() and not args.force:
            print("  건너뜀: 결과 파일이 이미 있다 (--force로 덮어쓴다)")
            continue

        with pymupdf.open(src) as doc:
            pages = len(doc)
            bad_watermark, bad_banner = clean(doc)
            # 쪽마다 하나씩 찾지 못하면 구조가 다른 책이므로 쓰지 않는다.
            if bad_watermark or bad_banner:
                if bad_watermark:
                    print(f"  오류: 워터마크가 하나가 아닌 쪽: {pages_text(bad_watermark)}", file=sys.stderr)
                if bad_banner:
                    print(f"  오류: 안내 문구가 한 줄이 아닌 쪽: {pages_text(bad_banner)}", file=sys.stderr)
                failed = True
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            doc.save(dst, garbage=4, deflate=True)

        left = leftovers(dst)
        if left:
            print(f"  오류: 저장한 파일에 {left}개가 남았다", file=sys.stderr)
            failed = True
        else:
            print(f"  {pages}쪽 → {dst.relative_to(ROOT) if dst.is_relative_to(ROOT) else dst}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
