"""교재 원본(과목 책)을 챕터별 PDF로 자른다: textbook/{과목}/{과목}_제NN장_{제목}.pdf

설계와 근거는 docs/changes/2026-10-21-split-textbook.md 참고.
기본은 계획 표만 보여 주고, --apply를 주어야 파일을 쓴다.
로그에는 교재 본문 텍스트를 출력하지 않는다 (워터마크 개인정보 보호). 출력하는 것은 목차의 장 제목, 머리말의 과목 이름, 쪽 번호뿐이다.
"""

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent.parent
SUBJECTS = json.loads((ROOT / "tools" / "subjects.json").read_text(encoding="utf-8"))

# 챕터 쪽 표시: 본문 쪽 꼬리말, 표지 다음 소단원 목록 쪽의 "강의보기". 빈 쪽·앞부분·판권에는 없다.
CONTENT_MARKERS = ("속전속결 필기 이론서", "강의보기")
WATERMARK_MIN_SIZE = 30  # 이 크기 이상 글자는 워터마크 [pt]
DIVIDER_DPI = 10
DIVIDER_RATIO = 0.5  # 한 색이 쪽의 이 비율 이상을 덮으면 표지
CHAPTER_RE = re.compile(r"^제(\d{2})장$")
HEADER_RE = re.compile(r"^(.+?)\s*\|\s*제\s*\d+\s*장")  # "회로이론 | 제1장. 전기이론"
TITLE_MIN_SIZE = 10  # 목차의 장 제목 글자 크기 하한 [pt]
TITLE_X_TOL = 3
# 제목 줄은 장 머리(또는 앞 제목 줄)에서 이만큼 아래에 있다 [pt].
# 더 가까운 줄은 같은 쪽에 겹쳐 인쇄된 다른 목차 층이라 건너뛴다 (회로이론 및 제어공학 p.13).
TITLE_GAP = (10, 20)
FILENAME_BAD = re.compile(r'[/\\:*?"<>|]')


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


@dataclass
class Line:
    x: float
    y: float
    size: float
    text: str


@dataclass
class Chapter:
    subject: str
    number: int
    title: str
    first: int  # 원본 쪽 (0부터)
    last: int

    @property
    def filename(self) -> str:
        return f"{self.subject}_제{self.number:02d}장_{self.title}.pdf"


@dataclass
class Book:
    path: Path
    name: str
    chapters: list[Chapter] = field(default_factory=list)
    skipped_dividers: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def page_lines(page: pymupdf.Page) -> list[Line]:
    """워터마크(큰 글자)를 뺀 줄 목록. 텍스트는 NFKC로 정규화한다 (수식용 기울임 글자 𝑅 → R)."""
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["size"] < WATERMARK_MIN_SIZE]
            text = unicodedata.normalize("NFKC", "".join(s["text"] for s in spans)).strip()
            if text:
                out.append(Line(line["bbox"][0], line["bbox"][1], max(s["size"] for s in spans), text))
    return out


def is_divider(page: pymupdf.Page) -> bool:
    """챕터 표지 쪽: 채도 있는 한 색이 쪽 대부분을 덮는다 (표지 색은 책마다 다르다)."""
    pix = page.get_pixmap(dpi=DIVIDER_DPI)
    s, c = pix.samples, pix.n
    counts = Counter((s[i] // 32, s[i + 1] // 32, s[i + 2] // 32) for i in range(0, len(s), c))
    color, n = counts.most_common(1)[0]
    return n / (pix.width * pix.height) >= DIVIDER_RATIO and max(color) - min(color) >= 2


def is_body(lines: list[Line]) -> bool:
    return any(m in l.text for l in lines for m in CONTENT_MARKERS)


def toc_entries(lines: list[Line]) -> dict[int, str]:
    """목차 쪽에서 {장 번호: 제목}. 제목은 "제NN장" 바로 아래 같은 x에 이어지는 큰 글자 줄들이다."""
    found = {}
    for head in lines:
        m = CHAPTER_RE.match(head.text)
        if not m:
            continue
        parts, y = [], head.y
        for l in sorted(lines, key=lambda l: l.y):
            if l.y <= y or abs(l.x - head.x) > TITLE_X_TOL or l.size < TITLE_MIN_SIZE:
                continue
            if CHAPTER_RE.match(l.text) or l.y - y > TITLE_GAP[1]:
                break
            if l.y - y < TITLE_GAP[0]:
                continue
            parts.append(l.text)
            y = l.y
        if parts:
            title = re.sub(r"\s+", " ", " ".join(parts)).strip()
            # 반각 가운뎃점(･)은 NFKC에서 ・가 된다. 파일명에는 보통 가운뎃점 ·를 쓰고, 줄바꿈으로 생긴 앞뒤 빈칸을 없앤다.
            found[int(m.group(1))] = re.sub(r"\s*[・·]\s*", "·", title)
    return found


def header_subject(lines: list[Line]) -> str | None:
    for l in lines:
        m = HEADER_RE.match(l.text)
        if m:
            return nfc(m.group(1).strip())
    return None


def analyze(path: Path) -> Book:
    """책 하나를 쪽마다 분류하고 챕터로 나눈다.

    앞부분(본문도 표지도 아닌 쪽이 이어지는 구간)에서 목차를 읽어 과목 하나의 장 목록을 얻는다.
    그 뒤 표지 쪽을 장 목록 순서대로 짝짓고, 각 장은 표지부터 다음 표지 전의 마지막 챕터 쪽(CONTENT_MARKERS)까지다.
    중간에 표시가 빠진 본문 쪽(회로이론 및 제어공학 p.168)은 포함되고,
    끝의 빈 쪽과 다음 과목 앞부분(표시가 없다)은 빠진다.
    """
    book = Book(path, nfc(path.stem).split(") ")[-1])
    doc = pymupdf.open(path)
    lines = [page_lines(p) for p in doc]
    divider = [is_divider(p) for p in doc]
    body = [is_body(ls) for ls in lines]

    pending: list[tuple[int, str]] = []  # 이번 과목에서 아직 표지와 짝짓지 않은 장 (번호, 제목)
    toc: dict[int, str] = {}
    i = 0
    while i < doc.page_count:
        if divider[i]:
            if toc:  # 앞부분이 끝났다 → 새 과목의 장 목록 확정
                pending, toc = sorted(toc.items()), {}
            if not pending:
                book.skipped_dividers.append(i)
                i += 1
                continue
            number, title = pending.pop(0)
            nxt = next((k for k in range(i + 1, doc.page_count) if divider[k]), doc.page_count)
            last = max([k for k in range(i, nxt) if body[k]], default=i)
            subjects = Counter(s for p in range(i, last + 1) if (s := header_subject(lines[p])))
            subject = subjects.most_common(1)[0][0] if subjects else None
            if subject is None:
                book.errors.append(f"제{number:02d}장: 머리말에서 과목 이름을 읽지 못했다 (원본 p.{i + 1}~{last + 1})")
                subject = "?"
            book.chapters.append(Chapter(subject, number, title, i, last))
            i = last + 1
            continue
        if not body[i]:
            for n, t in toc_entries(lines[i]).items():
                if n in toc and toc[n] != t:
                    book.errors.append(f"목차의 제{n:02d}장 제목이 쪽마다 다르다: {toc[n]!r} / {t!r}")
                toc.setdefault(n, t)
        i += 1
    if pending:
        book.errors.append(f"표지를 찾지 못한 장: {[n for n, _ in pending]}")
    check(book)
    return book


def check(book: Book) -> None:
    seen = set()
    by_subject: dict[str, list[int]] = {}
    for ch in book.chapters:
        by_subject.setdefault(ch.subject, []).append(ch.number)
        if ch.subject not in SUBJECTS:
            book.errors.append(f"tools/subjects.json에 없는 과목: {ch.subject}")
        if FILENAME_BAD.search(ch.title):
            book.errors.append(f"제목에 파일명으로 쓸 수 없는 글자: {ch.title!r}")
        if (ch.subject, ch.number) in seen:
            book.errors.append(f"{ch.subject} 제{ch.number:02d}장이 두 번 나왔다")
        seen.add((ch.subject, ch.number))
        if ch.last == ch.first:
            book.errors.append(f"{ch.subject} 제{ch.number:02d}장이 표지 한 쪽뿐이다")
    for subject, numbers in by_subject.items():
        if numbers != list(range(1, len(numbers) + 1)):
            book.errors.append(f"{subject} 장 번호가 1부터 연속되지 않는다: {numbers}")


def page_hashes(doc: pymupdf.Document, first: int = 0, last: int | None = None) -> list[str]:
    last = doc.page_count - 1 if last is None else last
    return [hashlib.md5(doc[p].get_text().encode()).hexdigest() for p in range(first, last + 1)]


def existing_for(out_dir: Path, ch: Chapter) -> list[Path]:
    """같은 과목·장 번호의 기존 파일 (제목이 달라도 찾는다)."""
    if not out_dir.is_dir():
        return []
    prefix = f"{ch.subject}_제{ch.number:02d}장_"
    return [p for p in out_dir.iterdir() if p.suffix == ".pdf" and nfc(p.name).startswith(prefix)]


def plan_and_apply(book: Book, out_root: Path, apply: bool) -> bool:
    doc = pymupdf.open(book.path)
    print(f"\n## {book.name} ({doc.page_count}쪽, 챕터 {len(book.chapters)}개)")
    if book.skipped_dividers:
        print(f"  장 목록 밖의 표지 쪽 (부록 등, 자르지 않음): 원본 p.{', '.join(str(p + 1) for p in book.skipped_dividers)}")
    ok = not book.errors
    for e in book.errors:
        print(f"  오류: {e}")
    print("  | 과목 | 장 | 제목 | 원본 쪽 | 쪽 수 | 기존 파일 |")
    print("  |---|---|---|---|---|---|")
    for ch in book.chapters:
        out_dir = out_root / ch.subject
        olds = existing_for(out_dir, ch)
        new_hash = page_hashes(doc, ch.first, ch.last)
        if not olds:
            state = "새로 만듦"
        elif len(olds) == 1 and nfc(olds[0].name) == ch.filename and page_hashes(pymupdf.open(olds[0])) == new_hash:
            state = "같음 (그대로 둠)"
        else:
            state = "다름! (덮어쓰지 않음): " + ", ".join(nfc(p.name) for p in olds)
            ok = False
        print(f"  | {ch.subject} | {ch.number:02d} | {ch.title} | p.{ch.first + 1}~{ch.last + 1} | {ch.last - ch.first + 1} | {state} |")
        if apply and book.errors == [] and state == "새로 만듦":
            out_dir.mkdir(parents=True, exist_ok=True)
            out = pymupdf.open()
            out.insert_pdf(doc, from_page=ch.first, to_page=ch.last)
            out.save(out_dir / ch.filename, garbage=4, deflate=True)
    if apply and book.errors:
        print("  오류가 있어 이 책은 자르지 않았다.")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", type=Path, default=[ROOT / "textbook_original"], help="원본 책 PDF 또는 폴더 (기본: textbook_original/)")
    ap.add_argument("--out", type=Path, default=ROOT / "textbook", help="챕터 PDF를 넣을 폴더 (기본: textbook/)")
    ap.add_argument("--apply", action="store_true", help="계획 표만 보이지 않고 실제로 자른다 (새 파일만 쓴다)")
    args = ap.parse_args()

    pdfs = []
    for p in args.inputs:
        pdfs += sorted(p.glob("*.pdf"), key=lambda x: nfc(x.name)) if p.is_dir() else [p]
    ok = all([plan_and_apply(analyze(pdf), args.out, args.apply) for pdf in pdfs])
    print("\n" + ("완료" if ok else "확인할 것이 있다 (오류 또는 다른 기존 파일)") + ("" if args.apply else " — 계획만 보였다. 자르려면 --apply"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
