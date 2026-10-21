"""교재 챕터 PDF의 「단원별 기출 문제풀이」로 문제지와 정답·해설 PDF를 만든다.

설계와 근거는 PLAN.md 참고. 로그에는 페이지 텍스트를 출력하지 않는다 (워터마크 개인정보 보호).
"""

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

# 교재 과목 → 과목 폴더. note-pdf와 함께 쓰는 tools/subjects.json에 있다 (프로젝트 CLAUDE.md의 과목 폴더 표와 맞춘다).
SUBJECT_DIRS = json.loads((Path(__file__).parent.parent / "subjects.json").read_text(encoding="utf-8"))

SECTION_TITLE = "기출 문제풀이"
BOX_FILL_TOL = 0.01  # 해설 상자 색 비교 허용오차 (RGB 0~1)
BOX_MIN_WIDTH = 100
BADGE_GAP = 15  # 해설 상자 아래 끝 ~ 「정답」 배지 위 끝 최대 간격 [pt]
ANSWER_LABEL = "정답"
ANSWER_SYMBOLS = "①②③④"
NUMBER_RE = re.compile(r"^\d{2}$")
NUMBER_MIN_SIZE = 14  # 문제 번호 글자 크기 하한 [pt]
COLUMN_TOL = 3  # 문제 번호 x와 해설 상자 x의 허용 차이 [pt]
MASK_PAD = 1.5
TABLE_ROWS = 25  # 정답표 한 단에 넣을 최대 행 수. 넘으면 두 단으로 나눈다


@dataclass
class Answer:
    page: int  # 0부터
    symbol: str  # "①" 등, 못 찾으면 ""
    label: pymupdf.Rect  # 「정답」 글자
    rect: pymupdf.Rect  # 가릴 영역 (배지 + 기호)
    box: pymupdf.Rect | None = None  # 이 배지가 붙은 해설 상자 (배지 바로 위)


@dataclass
class Problem:
    section: int  # 1부터
    number: int
    page: int
    answer: Answer | None = None


@dataclass
class Chapter:
    pdf: Path
    subject: str
    number: int
    title: str
    sections: list[list[int]] = field(default_factory=list)  # 기출 문제풀이 단원마다 페이지 (0부터)
    box_fill: tuple = ()
    boxes: dict[int, list[pymupdf.Rect]] = field(default_factory=dict)
    answers: list[Answer] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def pages(self) -> list[int]:
        return [p for s in self.sections for p in s]


def parse_name(pdf: Path) -> tuple[str, int, str]:
    m = re.match(r"(.+?)_제(\d+)장_(.+)$", pdf.stem)
    if not m:
        raise ValueError(f"파일명 형식이 아니다: {pdf.name}")
    return m.group(1), int(m.group(2)), m.group(3)


def has_label(page: pymupdf.Page) -> bool:
    return any(w[4] == ANSWER_LABEL for w in page.get_text("words"))


def find_sections(doc: pymupdf.Document) -> list[list[int]]:
    # 「기출 문제풀이」 제목 페이지에서 시작해, 「정답」이 있는 페이지가 이어지는 동안이 한 단원이다.
    # 전기기기처럼 한 장에 문제풀이가 여러 번(이론 → 문제 → 이론 → 문제) 나오는 교재가 있다.
    sections = []
    current = None
    for page in doc:
        if SECTION_TITLE in page.get_text():
            current = [page.number]
            sections.append(current)
        elif current is not None and has_label(page):
            current.append(page.number)
        else:
            current = None
    return sections


def find_filled(page: pymupdf.Page) -> list[tuple[tuple, pymupdf.Rect]]:
    return [(tuple(d["fill"]), pymupdf.Rect(d["rect"])) for d in page.get_drawings()
            if d.get("fill") and d["rect"].width >= BOX_MIN_WIDTH]


def same_color(a: tuple, b: tuple) -> bool:
    return all(abs(x - y) <= BOX_FILL_TOL for x, y in zip(a, b))


def box_above(label: pymupdf.Rect, rects: list[pymupdf.Rect]) -> pymupdf.Rect | None:
    # 「정답」 배지는 해설 상자 바로 아래 오른쪽 끝에 붙는다.
    near = [r for r in rects
            if r.x0 <= label.x0 and label.x1 <= r.x1 and 0 <= label.y0 - r.y1 <= BADGE_GAP]
    return min(near, key=lambda r: label.y0 - r.y1, default=None)


def drop_nested(rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    # 해설 안의 표·강조 영역이 같은 색인 경우가 있다 (전기설비기술기준). 바깥 상자만 남긴다.
    return [r for i, r in enumerate(rects)
            if not any(j != i and o.contains(r) and o != r for j, o in enumerate(rects))]


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
        answers.append(Answer(page.number, symbol, label,
                              rect + (-MASK_PAD, -MASK_PAD, MASK_PAD, MASK_PAD)))
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


def reading_key(page_no: int, rect: pymupdf.Rect, page_width: float, column_rect: pymupdf.Rect | None = None) -> tuple:
    # 단은 column_rect(기본은 rect 자신)의 왼쪽 끝으로 정한다. 정답 배지는 상자 오른쪽 끝에 있어서
    # 1단 편집(전기설비기술기준)에서도 오른쪽 단으로 보이므로, 배지가 붙은 상자를 기준으로 삼는다.
    column = 0 if (column_rect or rect).x0 < page_width / 2 else 1
    return (page_no, column, rect.y0)


def find_box_fill(ch: Chapter, doc: pymupdf.Document) -> tuple:
    # 해설 상자 색은 교재(과목)마다 다르다. 「정답」 배지 바로 위의 넓은 채움 사각형 색을 세어 가장 많은 것으로 정한다.
    votes = Counter()
    for a in ch.answers:
        filled = find_filled(doc[a.page])
        owner = box_above(a.label, [r for _, r in filled])
        if owner is not None:
            votes[next(tuple(round(c, 3) for c in f) for f, r in filled if r == owner)] += 1
    if not votes:
        raise ValueError(f"해설 상자 색을 정하지 못했다 (정답 배지 위에 채움 사각형이 없다): {ch.pdf.name}")
    if len(votes) > 1:
        ch.warnings.append(f"정답 배지 위 상자 색이 {len(votes)}가지다: {dict(votes)}")
    return votes.most_common(1)[0][0]


def analyze(pdf: Path) -> tuple[Chapter, pymupdf.Document]:
    subject, number, title = parse_name(pdf)
    ch = Chapter(pdf, subject, number, title)
    doc = pymupdf.open(pdf)

    ch.sections = find_sections(doc)
    if not ch.sections:
        raise ValueError(f"「{SECTION_TITLE}」 페이지를 찾지 못했다: {pdf.name}")

    for pno in ch.pages:
        ch.answers += find_answers(doc[pno])
    ch.box_fill = find_box_fill(ch, doc)
    for pno in ch.pages:
        ch.boxes[pno] = drop_nested([r for f, r in find_filled(doc[pno]) if same_color(f, ch.box_fill)])
    for a in ch.answers:
        a.box = box_above(a.label, ch.boxes[a.page])

    column_xs = sorted({round(b.x0) for bs in ch.boxes.values() for b in bs})
    for sno, pages in enumerate(ch.sections, 1):
        events = []
        for pno in pages:
            width = doc[pno].rect.width
            for n, r in find_numbers(doc[pno], column_xs):
                events.append((reading_key(pno, r, width), "number", n))
            for b in ch.boxes[pno]:
                events.append((reading_key(pno, b, width), "box", b))
            for a in (a for a in ch.answers if a.page == pno):
                events.append((reading_key(pno, a.rect, width, a.box), "answer", a))
        events.sort(key=lambda e: e[0])
        check_order(ch, sno, events)

        # V2
        numbers = [p.number for p in ch.problems if p.section == sno]
        if numbers != list(range(1, len(numbers) + 1)):
            ch.warnings.append(f"{section_label(ch, sno)}문제 번호가 01부터 연속되지 않는다: {numbers}")
    for p in ch.problems:
        if p.answer is None or not p.answer.symbol:
            ch.warnings.append(f"{problem_label(ch, p)}번 정답을 찾지 못했다 (PDF p.{p.page + 1})")
    return ch, doc


def check_order(ch: Chapter, sno: int, events: list) -> None:
    # V1: 읽기 순서로 「문제 번호 → 해설 상자 1개 이상 → 정답」이 반복되어야 한다.
    # 해설 상자가 여럿인 것은 해설이 다음 단·페이지로 넘어간 경우다 (넘어간 앞부분 상자에는 정답 배지가 없다).
    # 정답은 이 문제 번호에 붙인다. 그래서 해설이 다음 페이지로 넘어가도 짝이 맞는다.
    state = "start"
    for key, kind, value in events:
        where = f"PDF p.{key[0] + 1}"
        if kind == "number":
            if state in ("number", "box"):
                ch.warnings.append(f"{where}: {problem_label(ch, ch.problems[-1])}번이 정답 없이 끝났다")
            ch.problems.append(Problem(sno, value, key[0]))
            state = "number"
        elif kind == "box":
            if state not in ("number", "box"):
                ch.warnings.append(f"{where}: 문제 번호 없이 나온 해설 상자")
            state = "box"
        else:
            if state != "box":
                ch.warnings.append(f"{where}: 해설 상자 뒤에 오지 않은 정답")
            elif value.box is None:
                ch.warnings.append(f"{where}: 바로 위에 해설 상자가 없는 정답")
            if ch.problems and ch.problems[-1].section == sno and ch.problems[-1].answer is None:
                ch.problems[-1].answer = value
            state = "answered"
    if state in ("number", "box"):
        ch.warnings.append(f"{problem_label(ch, ch.problems[-1])}번이 정답 없이 끝났다")


def section_label(ch: Chapter, sno: int) -> str:
    return f"({sno}) " if len(ch.sections) > 1 else ""


def problem_label(ch: Chapter, p: Problem) -> str:
    return f"{section_label(ch, p.section)}{p.number:02d}"


def build_questions(ch: Chapter, doc: pymupdf.Document, dpi: int) -> pymupdf.Document:
    out = pymupdf.open()
    for pno in ch.pages:
        page = doc[pno]
        masks = ch.boxes[pno] + [a.rect for a in ch.answers if a.page == pno]
        for r in masks:
            page.draw_rect(r, color=None, fill=(1, 1, 1), overlay=True)
        pix = page.get_pixmap(dpi=dpi)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, pixmap=pix)
    return out


def page_ranges(ch: Chapter) -> str:
    return ", ".join(f"PDF p.{s[0] + 1}~{s[-1] + 1}" for s in ch.sections)


def build_answers(ch: Chapter, src: Path) -> pymupdf.Document:
    out = pymupdf.open()
    page = out.new_page(width=556, height=754)
    def row(p: Problem) -> str:
        symbol = p.answer.symbol if p.answer and p.answer.symbol else "?"
        pno = p.answer.page if p.answer else p.page
        # 해설 위치: 이 파일의 쪽 (정답표가 1쪽) / 교재 원본 PDF 페이지
        return (f"<tr><td>{problem_label(ch, p)}</td><td>{symbol}</td>"
                f"<td>{ch.pages.index(pno) + 2}쪽 (교재 PDF p.{pno + 1})</td></tr>")

    head = f"""
    <h2>{ch.subject} 제{ch.number}장 {ch.title} — 정답</h2>
    <p>해설은 2쪽부터 (교재 원본 {page_ranges(ch)})</p>
    """
    # 문제가 많으면(전기기기 한 장 40문제) 한 단에 다 들어가지 않으므로 두 단으로 나눈다.
    columns = 1 if len(ch.problems) <= TABLE_ROWS else 2
    per = -(-len(ch.problems) // columns)
    tables = ["<table><tr><th>번호</th><th>정답</th><th>해설 위치</th></tr>"
              + "".join(row(p) for p in ch.problems[i:i + per]) + "</table>"
              for i in range(0, len(ch.problems), per)]
    pad = "14pt" if columns == 1 else "6pt"
    css = f"""
    * {{ font-family: sans-serif; }}
    h2 {{ font-size: 16pt; }}
    p {{ font-size: 10pt; color: #555; }}
    table {{ border-collapse: collapse; }}
    th, td {{ border: 1px solid #999; padding: 3pt {pad}; font-size: 12pt; text-align: center; white-space: nowrap; }}
    th {{ background: #eee; }}
    """
    area = page.rect + (50, 50, -50, -50)
    if columns == 1:
        page.insert_htmlbox(area, head + tables[0], css=css)
    else:
        page.insert_htmlbox(pymupdf.Rect(area.x0, area.y0, area.x1, area.y0 + 70), head, css=css)
        half = area.width / 2
        for i, t in enumerate(tables):
            x0 = area.x0 + i * half
            page.insert_htmlbox(pymupdf.Rect(x0, area.y0 + 70, x0 + half - 6, area.y1), t, css=css)
    with pymupdf.open(src) as original:
        for s in ch.sections:
            out.insert_pdf(original, from_page=s[0], to_page=s[-1])
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
          f"페이지 {page_ranges(ch)}, 가린 해설 상자 {masked}개 → {q_path.name}, {a_path.name}")
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
