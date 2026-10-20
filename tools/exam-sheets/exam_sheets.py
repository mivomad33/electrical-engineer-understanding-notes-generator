"""교재 챕터 PDF의 「단원별 기출 문제풀이」로 문제지와 정답·해설 PDF를 만든다.

설계와 근거는 PLAN.md 참고. 로그에는 페이지 텍스트를 출력하지 않는다 (워터마크 개인정보 보호).
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

# 프로젝트 CLAUDE.md의 과목 폴더 표와 맞춘다.
SUBJECT_DIRS = {"회로이론": "circuit-theory"}

SECTION_TITLE = "기출 문제풀이"
BOX_FILL = (0.98, 0.941, 0.898)  # 해설 상자 베이지
BOX_FILL_TOL = 0.01
BOX_MIN_WIDTH = 100
ANSWER_LABEL = "정답"
ANSWER_SYMBOLS = "①②③④"
NUMBER_RE = re.compile(r"^\d{2}$")
NUMBER_MIN_SIZE = 14  # 문제 번호 글자 크기 하한 [pt]
COLUMN_TOL = 3  # 문제 번호 x와 해설 상자 x의 허용 차이 [pt]
MASK_PAD = 1.5


@dataclass
class Answer:
    page: int  # 0부터
    symbol: str  # "①" 등, 못 찾으면 ""
    rect: pymupdf.Rect  # 가릴 영역 (배지 + 기호)


@dataclass
class Problem:
    number: int
    page: int
    answer: Answer | None = None


@dataclass
class Chapter:
    pdf: Path
    subject: str
    number: int
    title: str
    start: int = -1  # 문제 영역 첫 페이지 (0부터)
    boxes: dict[int, list[pymupdf.Rect]] = field(default_factory=dict)
    answers: list[Answer] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_name(pdf: Path) -> tuple[str, int, str]:
    m = re.match(r"(.+?)_제(\d+)장_(.+)$", pdf.stem)
    if not m:
        raise ValueError(f"파일명 형식이 아니다: {pdf.name}")
    return m.group(1), int(m.group(2)), m.group(3)


def find_boxes(page: pymupdf.Page) -> list[pymupdf.Rect]:
    boxes = []
    for d in page.get_drawings():
        fill = d.get("fill")
        if not fill or d["rect"].width < BOX_MIN_WIDTH:
            continue
        if all(abs(a - b) <= BOX_FILL_TOL for a, b in zip(fill, BOX_FILL)):
            boxes.append(pymupdf.Rect(d["rect"]))
    return boxes


def find_answers(page: pymupdf.Page) -> list[Answer]:
    words = page.get_text("words")
    drawings = page.get_drawings()
    answers = []
    for w in words:
        if w[4] != ANSWER_LABEL:
            continue
        label = pymupdf.Rect(w[:4])
        cy = (label.y0 + label.y1) / 2
        rect = pymupdf.Rect(label)
        symbol = ""
        for v in words:
            r = pymupdf.Rect(v[:4])
            if (v[4] in ANSWER_SYMBOLS and abs((r.y0 + r.y1) / 2 - cy) < 3
                    and 0 <= r.x0 - label.x1 < 20):
                symbol = v[4]
                rect |= r
        # 배지 배경(둥근 사각형)이 글자보다 약간 크므로 겹치는 작은 도형까지 덮는다.
        for d in drawings:
            dr = d["rect"]
            if d.get("fill") and dr.intersects(label) and dr.width < 40 and dr.height < 20:
                rect |= dr
        answers.append(Answer(page.number, symbol, rect + (-MASK_PAD, -MASK_PAD, MASK_PAD, MASK_PAD)))
    return answers


def find_numbers(page: pymupdf.Page, column_xs: list[float]) -> list[tuple[int, pymupdf.Rect]]:
    found = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if tuple(round(c) for c in line["dir"]) != (1, 0):
                continue  # 기울어진 글자 = 워터마크
            for span in line["spans"]:
                text = span["text"].strip()
                r = pymupdf.Rect(span["bbox"])
                if (NUMBER_RE.match(text) and span["size"] >= NUMBER_MIN_SIZE
                        and any(abs(r.x0 - x) <= COLUMN_TOL for x in column_xs)):
                    found.append((int(text), r))
    return found


def reading_key(page_no: int, rect: pymupdf.Rect, page_width: float) -> tuple:
    column = 0 if rect.x0 < page_width / 2 else 1
    return (page_no, column, rect.y0)


def analyze(pdf: Path) -> tuple[Chapter, pymupdf.Document]:
    subject, number, title = parse_name(pdf)
    ch = Chapter(pdf, subject, number, title)
    doc = pymupdf.open(pdf)

    for page in doc:
        if SECTION_TITLE in page.get_text():
            ch.start = page.number
            break
    if ch.start < 0:
        raise ValueError(f"「{SECTION_TITLE}」 페이지를 찾지 못했다: {pdf.name}")

    for pno in range(ch.start, doc.page_count):
        page = doc[pno]
        ch.boxes[pno] = find_boxes(page)
        page_answers = find_answers(page)
        ch.answers += page_answers
        if len(ch.boxes[pno]) != len(page_answers):  # V1
            ch.warnings.append(
                f"PDF p.{pno + 1}: 해설 상자 {len(ch.boxes[pno])}개, 정답 {len(page_answers)}개")

    column_xs = sorted({round(b.x0) for bs in ch.boxes.values() for b in bs})
    events = []
    for pno in range(ch.start, doc.page_count):
        width = doc[pno].rect.width
        for n, r in find_numbers(doc[pno], column_xs):
            events.append((reading_key(pno, r, width), "number", n))
        for a in (a for a in ch.answers if a.page == pno):
            events.append((reading_key(pno, a.rect, width), "answer", a))
    events.sort(key=lambda e: e[0])

    # 정답은 읽기 순서상 바로 앞의, 아직 짝이 없는 문제 번호에 붙인다.
    for key, kind, value in events:
        if kind == "number":
            ch.problems.append(Problem(value, key[0]))
        elif ch.problems and ch.problems[-1].answer is None:
            ch.problems[-1].answer = value
        else:
            ch.warnings.append(f"PDF p.{value.page + 1}: 짝지을 문제 번호가 없는 정답")

    # V2
    numbers = [p.number for p in ch.problems]
    if numbers != list(range(1, len(numbers) + 1)):
        ch.warnings.append(f"문제 번호가 01부터 연속되지 않는다: {numbers}")
    for p in ch.problems:
        if p.answer is None or not p.answer.symbol:
            ch.warnings.append(f"{p.number:02d}번 정답을 찾지 못했다 (PDF p.{p.page + 1})")
    return ch, doc


def build_questions(ch: Chapter, doc: pymupdf.Document, dpi: int) -> pymupdf.Document:
    out = pymupdf.open()
    for pno in range(ch.start, doc.page_count):
        page = doc[pno]
        masks = ch.boxes[pno] + [a.rect for a in ch.answers if a.page == pno]
        for r in masks:
            page.draw_rect(r, color=None, fill=(1, 1, 1), overlay=True)
        pix = page.get_pixmap(dpi=dpi)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, pixmap=pix)
    return out


def build_answers(ch: Chapter, src: Path) -> pymupdf.Document:
    out = pymupdf.open()
    page = out.new_page(width=556, height=754)
    def row(p: Problem) -> str:
        symbol = p.answer.symbol if p.answer and p.answer.symbol else "?"
        pno = p.answer.page if p.answer else p.page
        # 해설 위치: 이 파일의 쪽 (정답표가 1쪽) / 교재 원본 PDF 페이지
        return (f"<tr><td>{p.number:02d}</td><td>{symbol}</td>"
                f"<td>{pno - ch.start + 2}쪽 (교재 PDF p.{pno + 1})</td></tr>")

    rows = "".join(row(p) for p in ch.problems)
    html = f"""
    <h2>{ch.subject} 제{ch.number}장 {ch.title} — 정답</h2>
    <p>해설은 2쪽부터 (교재 원본 PDF p.{ch.start + 1}~)</p>
    <table><tr><th>번호</th><th>정답</th><th>해설 위치</th></tr>{rows}</table>
    """
    css = """
    * { font-family: sans-serif; }
    h2 { font-size: 16pt; }
    p { font-size: 10pt; color: #555; }
    table { border-collapse: collapse; }
    th, td { border: 1px solid #999; padding: 3pt 14pt; font-size: 12pt; text-align: center; white-space: nowrap; }
    th { background: #eee; }
    """
    page.insert_htmlbox(page.rect + (50, 50, -50, -50), html, css=css)
    with pymupdf.open(src) as original:
        out.insert_pdf(original, from_page=ch.start, to_page=original.page_count - 1)
    return out


def verify_questions(path: Path) -> list[str]:  # V3
    with pymupdf.open(path) as d:
        text = "".join(p.get_text() for p in d).strip()
    return [f"{path.name}에 텍스트가 남아 있다"] if text else []


def process(pdf: Path, out_root: Path, dpi: int) -> bool:
    ch, doc = analyze(pdf)
    out_dir = out_root / SUBJECT_DIRS.get(ch.subject, ch.subject)
    out_dir.mkdir(parents=True, exist_ok=True)
    q_path = out_dir / f"ch{ch.number:02d}-questions.pdf"
    a_path = out_dir / f"ch{ch.number:02d}-answers.pdf"

    build_questions(ch, doc, dpi).save(q_path, garbage=4, deflate=True)
    build_answers(ch, pdf).save(a_path, garbage=4, deflate=True)
    doc.close()
    ch.warnings += verify_questions(q_path)

    masked = sum(len(b) for b in ch.boxes.values())
    print(f"ch{ch.number:02d} {ch.title}: 문제 {len(ch.problems)}개, "
          f"페이지 PDF p.{ch.start + 1}~, 가린 해설 상자 {masked}개 → {q_path.name}, {a_path.name}")
    for w in ch.warnings:
        print(f"  경고: {w}")
    return not ch.warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", type=Path, help="챕터 PDF 또는 과목 폴더")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "output")
    args = ap.parse_args()

    pdfs = []
    for p in args.inputs:
        pdfs += sorted(p.glob("*.pdf")) if p.is_dir() else [p]
    ok = all([process(pdf, args.out, args.dpi) for pdf in pdfs])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
