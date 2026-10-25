"""교재 챕터 PDF에서 그림을 잘라 노트에 넣을 PNG로 만든다: notes/{과목 폴더}/figures/chNN/

설계와 근거는 docs/changes/2026-10-25-textbook-figures.md, 사용법은 README.md 참고.
노트에 반드시 넣을 그림(필수)은 넷이다.
- 캡션 그림 figN-M: 캡션 「그림 N-M」 위의 도형·이미지와 그 안팎의 라벨을 한 영역으로 묶는다.
- 그림이 든 표 tabN-M: 「표 N-M」 표 안에 곡선·사선·이미지(파형, 소자 기호 등)가 있는 표. 마크다운 표로 옮길 수 없다.
- 캡션 없는 그림 pPP-k: 캡션이 없는 도형 덩어리 (TIP 상자 안 그림, 단계별 회로 등).
- 직접 지정: manual.json의 "add". manual.json으로 자동 결과를 빼거나(exclude) 합치거나(merge) 영역을 고치기도(rect) 한다.
그림 없는 표와 표시 없는 표(루스 표, 공식 상자)는 「후보」로 보고만 한다.
기출 문제풀이 쪽은 대상이 아니다 (문제지 PDF가 따로 있다).
로그와 결과 파일에는 교재 본문 텍스트를 넣지 않는다 (워터마크·응원 문구 개인정보 보호). 캡션 제목만 예외다.
"""

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent.parent
SUBJECTS = json.loads((ROOT / "tools" / "subjects.json").read_text(encoding="utf-8"))

NOTES = ROOT / "notes"  # --out으로 바꿀 수 있다
WORK = ROOT / ".cache" / "work"
DPI = 300  # PNG 해상도. note-pdf는 PNG에 적힌 DPI로 원래 크기를 되살린다
BODY_TOP = 60  # 머리말(과 맨 위 응원 문구) 아래 [pt]
BODY_BOTTOM = 60  # 꼬리말 위, 쪽 아래 끝에서 [pt]
SIDE_TAB = 45  # 오른쪽 끝 장 번호 탭 너비 [pt]
CROP_SIDE = 15  # 잘라 낼 때 오른쪽 끝에서 비우는 너비 [pt]
CAPTION_RE = re.compile(r"^그림\s*(\d+)\s*[-–−]\s*(\d+)\s*(.*)$")
TABLE_RE = re.compile(r"^표\s*(\d+)\s*[-–−]\s*(\d+)$")
MANUAL = Path(__file__).resolve().parent / "manual.json"
SECTION_TITLE = "기출 문제풀이"  # exam-sheets와 같은 기준
ANSWER_LABEL = "정답"
BODY_FOOTER = "속전속결 필기 이론서"
LECTURE_MARK = "강의보기"  # 장 표지 다음 소단원 목록 쪽 (QR 코드)
FIRST_GAP = 45  # 캡션(과 그 위 소제목 줄) 위 끝 ~ 그림 아래 끝 최대 간격 [pt]
CHAIN_GAP = 22  # 그림을 이루는 도형끼리의 최대 세로 간격 [pt]
SIDE_GAP = 60  # 그림을 이루는 도형끼리의 최대 가로 간격 (사이에 ⇨ 같은 글자가 들어간다) [pt]
LABEL_GAP = 12  # 그림 밖 라벨이 그림에서 떨어질 수 있는 최대 간격 [pt]
TIGHT_GAP = 4  # 본문 크기 글자를 그림 위 라벨로 볼 최대 간격 [pt] (그림 위 본문 수식 줄과 구별)
SUB_GAP = 20  # 캡션과 그 위 소제목·설명 줄 사이, 소제목 줄끼리의 최대 간격 [pt]
BODY_TEXT_SIZE = 9.5  # 캡션 첫 글자는 이보다 작다 (본문 글자는 9~10) [pt]
LABEL_MAX_CHARS = 14
CAPTION_SIZE = 8.95  # 그림 라벨·소제목·캡션 글자는 이보다 작다 (본문은 9~10, 8.5~8.9는 라벨에만 나온다) [pt]
PAD = 4  # 잘라낼 때 둘레 여백 [pt]
UNCAPTIONED_MIN = (30, 5)  # 캡션 없는 그림의 최소 크기: 긴 변, 짧은 변 [pt]
TABLE_RULE = 0.35  # 표 칸선으로 보는 길이: 표 너비(세로선은 높이)에 대한 비율
TABLE_ROW = 18  # 표 칸선 사이 최대 간격 (한 줄 높이) [pt]
FIGURE_FONTS = ("Batang",)  # 그림 속 라벨 글꼴 (BatangChe 포함). 본문·소제목·수식 글꼴과 겹치지 않는다
ARROWS_RE = re.compile(r"[\s⇦⇧⇨⇩⇐⇑⇒⇓←↑→↓➡⬅⬆⬇×]+")  # 하위 그림 사이의 화살표·곱셈 기호만 있는 줄
PRIVATE_RE = re.compile(r"@|\d{7,}|응원합니다")  # 잘라낸 영역에 이런 글자가 있으면 멈춘다


@dataclass
class Line:
    rect: pymupdf.Rect
    size: float  # 줄에서 가장 큰 글자
    text: str
    first_size: float  # 첫 글자 (캡션 판정용: 캡션 안 수식 글자는 더 크다)
    figure_font: bool = False  # 그림 속 글자 글꼴(바탕체 계열)로 쓴 줄. 본문은 이 글꼴을 쓰지 않는다


@dataclass
class Figure:
    id: str  # 파일 이름: "fig4-1", "tab4-2", 캡션 없는 그림 "p05-1"(PDF 쪽-순번), 직접 지정한 이름
    kind: str  # figure | table | uncaptioned | manual | text-table (마지막만 후보)
    page: int  # 0부터
    rect: pymupdf.Rect
    title: str = ""

    @property
    def label(self) -> str:
        if self.kind in ("table", "text-table") and not self.id.startswith("tab"):
            return "표시 없는 표"
        return {"figure": f"그림 {self.id[3:]}", "table": f"표 {self.id[3:]}", "text-table": f"표 {self.id[3:]}"}.get(
            self.kind, "캡션 없음")


@dataclass
class Chapter:
    pdf: Path
    book: str
    subject: str
    number: int
    figures: list[Figure] = field(default_factory=list)  # 필수
    candidates: list[Figure] = field(default_factory=list)  # 그림 없는 표 (필수 아님, manual.json add로 넣을 수 있다)
    body_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def out_dir(self) -> Path:
        return NOTES / self.subject / "figures" / f"ch{self.number:02d}"

    @property
    def audit_dir(self) -> Path:
        return WORK / self.subject / f"ch{self.number:02d}-figures"


pymupdf.Rect.mid = property(lambda r: pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))


def rel(p: Path) -> Path:
    return p.relative_to(ROOT) if p.is_relative_to(ROOT) else p


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip()


def clean_title(s: str) -> str:
    # 캡션 안 수식은 사용자 정의 영역 글자로 깨져 나온다 → "…"로 바꾼다
    return re.sub(r"[\ue000-\uf8ff]+", "…", nfkc(s))


def parse_name(pdf: Path) -> tuple[str, int]:
    m = re.match(r"(.+?)_제(\d+)장_", unicodedata.normalize("NFC", pdf.name))
    if not m:
        raise ValueError(f"파일명 형식이 아니다: {pdf.name}")
    return m.group(1), int(m.group(2))


def exam_pages(doc: pymupdf.Document) -> set[int]:
    # 「기출 문제풀이」 제목 쪽부터 「정답」 배지가 있는 쪽이 이어지는 동안 (exam-sheets의 find_sections와 같다)
    pages, inside = set(), False
    for page in doc:
        text = page.get_text()
        if SECTION_TITLE in text:
            inside = True
        elif not (inside and any(w[4] == ANSWER_LABEL for w in page.get_text("words"))):
            inside = False
        if inside:
            pages.add(page.number)
    return pages


def thick(r: pymupdf.Rect) -> pymupdf.Rect:
    # 가로·세로 선은 넓이가 0인 사각형이라 PyMuPDF가 빈 사각형으로 보고 겹침·합치기에서 빠뜨린다 → 0.5pt씩 두께를 준다
    if r.width < 1:
        r = pymupdf.Rect(r.x0 - 0.5, r.y0, r.x1 + 0.5, r.y1)
    if r.height < 1:
        r = pymupdf.Rect(r.x0, r.y0 - 0.5, r.x1, r.y1 + 0.5)
    return r


def body_area(page: pymupdf.Page) -> pymupdf.Rect:
    r = page.rect
    return pymupdf.Rect(0, BODY_TOP, r.width - SIDE_TAB, r.height - BODY_BOTTOM)


def page_lines(page: pymupdf.Page) -> list[Line]:
    area = body_area(page)
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = nfkc("".join(s["text"] for s in line["spans"]))
            rect = pymupdf.Rect(line["bbox"])
            if text and rect.intersects(area):
                chars = [(len(s["text"].strip()), s["font"].startswith(FIGURE_FONTS)) for s in line["spans"]]
                fig = sum(n for n, f in chars if f) > 0.5 * sum(n for n, _ in chars)
                out.append(Line(rect, max(s["size"] for s in line["spans"]), text, line["spans"][0]["size"], fig))
    return out


def page_graphics(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, dict | None]]:
    """본문 영역 안의 도형과 이미지 (사각형, 원래 도형 정보 — 이미지는 None)."""
    area = body_area(page)
    out = []
    def inside(r: pymupdf.Rect) -> bool:
        # 대부분이 본문 영역 안에 있어야 한다 (오른쪽 끝 장 번호 탭, 머리말 장식 제외)
        return (r & area).width * (r & area).height >= 0.5 * r.width * r.height and r.width <= 0.85 * page.rect.width

    for d in page.get_drawings():
        r = thick(pymupdf.Rect(d["rect"]))
        if r.width + r.height >= 2 and inside(r):
            out.append((r, d))
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"])
        if min(r.width, r.height) >= 3 and inside(r):
            out.append((r, None))
    return out


def is_text_box(d: dict | None, rect: pymupdf.Rect, lines: list[Line]) -> bool:
    """공식 강조 띠·TIP 상자처럼 글을 담은 사각형인가 (그림이 아니라 본문의 일부).

    테두리 없이 칠만 한 사각형이 글을 담고 있으면 강조 띠, 그 밖의 상자는 본문 줄을 담고 있으면 상자다
    (TIP 상자는 칠과 테두리를 따로 그리기도 한다).
    """
    if d is None:
        return False
    if any(it[0] not in ("re", "qu", "l", "c") for it in d["items"]):
        return False
    if any(it[0] in ("l", "c") for it in d["items"]) and (rect.width < 40 or rect.height < 15):
        return False  # 화살촉 같은 작은 다각형 (모서리를 둥글린 상자만 선·곡선으로 그려진다)
    inside = [ln for ln in lines if rect.contains(ln.rect.mid)]
    if not inside:
        return False
    if d.get("fill") is not None and d.get("color") is None:
        return True
    return any(is_body_line(ln) for ln in inside)


def is_fraction_bar(r: pymupdf.Rect, lines: list[Line]) -> bool:
    """수식의 분수 막대: 짧은 가로선 바로 위에 분자, 바로 아래에 분모가 있다."""
    if r.height > 1.5 or r.width > 120:
        return False
    def over(ln: Line) -> bool:
        # 분자·분모는 막대 가운데에 있다
        centered = abs(ln.rect.mid.x - r.mid.x) <= 0.25 * r.width + 2
        return x_overlap(ln.rect, r.x0, r.x1) >= 0.6 * max(ln.rect.width, 0.1) and centered and not ln.figure_font
    up = [ln for ln in lines if r.y0 - 7 <= ln.rect.y1 <= r.y0 + 2 and over(ln)]
    down = [ln for ln in lines if r.y1 - 2 <= ln.rect.y0 <= r.y1 + 7 and over(ln)]
    # 분자나 분모 중 하나는 막대 길이에 견줄 만하다 (축 위아래의 작은 라벨과 구별)
    return bool(up and down) and any(ln.rect.width >= 0.4 * r.width for ln in up + down)


def is_formula_mark(d: dict | None, r: pymupdf.Rect, lines: list[Line]) -> bool:
    """수식에 딸린 도형: 글자 뒤에 깐 형광 띠, 행렬·절댓값의 세로 막대(칠한 가는 사각형)."""
    if d is None or any(it[0] != "re" for it in d["items"]) or d.get("fill") is None or d.get("color") is not None:
        return False
    if r.width <= 2.5 and r.height >= 8:
        # 그림의 선은 칠한 사각형으로 그리지 않는다 (전 과목 그림 안에 하나도 없다)
        return any(min(ln.rect.y1, r.y1) - max(ln.rect.y0, r.y0) >= 0.4 * r.height and not ln.figure_font
                   and (r.x0 - 6 <= ln.rect.x1 <= r.x0 + 2 or r.x1 - 2 <= ln.rect.x0 <= r.x1 + 6) for ln in lines)
    covered = sum((ln.rect & r).width * (ln.rect & r).height for ln in lines if ln.rect.intersects(r) and not ln.figure_font)
    return covered >= 0.5 * r.width * r.height


def is_decoration(r: pymupdf.Rect, lines: list[Line]) -> bool:
    """본문 줄에 붙은 밑줄·형광 표시."""
    return any(is_body_line(ln) and r.intersects(ln.rect + (-2, -2, 2, 2)) and r.height <= ln.rect.height + 4
               for ln in lines)


def drop_isolated_text_rects(graphics, lines: list[Line]):
    """글만 담고 다른 도형과 닿지 않는 사각형(공식 테두리 상자 등)을 뺀다. 블록선도의 블록은 화살표가 닿아 있어 남는다."""
    out = []
    for i, (r, d) in enumerate(graphics):
        if d is not None and all(it[0] in ("re", "qu") for it in d["items"]) and r.width > 20 and r.height > 8:
            inside = [ln for ln in lines if r.contains(ln.rect.mid)]
            # 같은 사각형을 칠·테두리로 두 번 그린 것은 닿은 것으로 치지 않는다
            touching = any(j != i and (r + (-3, -3, 3, 3)).intersects(o) and not r.contains(o)
                           and not (o + (-1, -1, 1, 1)).contains(r) for j, (o, _) in enumerate(graphics))
            if inside and not touching:
                continue
        out.append((r, d))
    return out


def is_body_line(ln: Line) -> bool:
    # 본문 줄: 한글이 든 긴 줄, 본문 크기 글자의 한글 줄(소제목), 아주 넓은 줄.
    # 그림 안 라벨은 한글이면 글자가 작고, 수식 라벨(v_R(t) 등)은 글자가 커도 한글이 없고 짧다
    if ln.figure_font:
        return False
    hangul = re.search(r"[가-힣]", ln.text) is not None
    return hangul and (len(ln.text) >= 10 or ln.first_size >= BODY_TEXT_SIZE) or ln.rect.width > 150


def is_drawing(d: dict | None, r: pymupdf.Rect) -> bool:
    """표 칸선과 달리 그림을 이루는 요소인가: 이미지, 곡선, 사선."""
    if d is None:
        return min(r.width, r.height) >= 8
    return any(it[0] == "c" or it[0] == "l" and abs(it[1].x - it[2].x) > 0.5 and abs(it[1].y - it[2].y) > 0.5
               for it in d["items"])


def x_overlap(a: pymupdf.Rect, x0: float, x1: float) -> float:
    return max(0.0, min(a.x1, x1) - max(a.x0, x0))


def find_region(cap: Line, span: tuple[float, float], graphics, lines: list[Line], barriers: list[Line], rules=()):
    """캡션 위로 이어지는 도형 사슬을 모으고, 그 안팎의 라벨을 더한 영역."""
    x0, x1 = span

    def in_span(r: pymupdf.Rect) -> bool:
        return r.width == 0 and x0 <= r.x0 <= x1 or x_overlap(r, x0, x1) >= 0.5 * max(r.width, 0.1)

    def blocked(top: float, bottom: float, r: pymupdf.Rect) -> bool:
        # top~bottom 사이에 본문 줄이나 다른 캡션이 가로로 겹쳐 끼어 있으면 같은 그림이 아니다
        return any(top - 1 <= b.rect.y0 and b.rect.y1 <= bottom + 1 and x_overlap(b.rect, r.x0, r.x1) > 0
                   for b in barriers)

    pool = [r for r, _ in graphics if in_span(r) and r.y1 <= cap.rect.y0 + 1]
    # 캡션과 그림 사이의 소제목 줄([회로], (a) 등)을 건너 올라간다
    bottom = cap.rect.y0
    while True:
        subs = [ln for ln in lines if in_span(ln.rect) and bottom - SUB_GAP <= ln.rect.y1 <= bottom and ln.rect.y0 < bottom - 1
                and (ln.first_size < CAPTION_SIZE or not is_body_line(ln) and len(ln.text) <= LABEL_MAX_CHARS)]
        if not subs:
            break
        bottom = min(ln.rect.y0 for ln in subs)
    # 그림은 캡션 위에 놓인다: 캡션과 가로로 겹치거나 SIDE_GAP 안에 있는 도형에서 시작한다 (옆 단의 수식은 빼기 위함)
    above = [r for r in pool if not blocked(r.y1, cap.rect.y0, r)
             and r.x0 <= cap.rect.x1 + SIDE_GAP and r.x1 >= cap.rect.x0 - SIDE_GAP]
    seeds = [r for r in above if bottom - FIRST_GAP <= r.y1]
    if not seeds and above:
        # 옆 그림과 나란히 놓여 캡션 높이가 맞춰진 작은 그림은 캡션에서 더 떨어져 있다
        low = max(r.y1 for r in above)
        if bottom - low <= 2 * FIRST_GAP:
            seeds = [r for r in above if r.y1 >= low - CHAIN_GAP]
    if not seeds:
        return None
    region = pymupdf.Rect(seeds[0])
    for r in seeds[1:]:
        region |= r
    # 하위 그림 사이의 소제목 줄((a) …, [회로] 등 작은 글자·그림 글꼴)은 사슬을 끊지 않는다
    labels = [ln.rect for ln in lines if in_span(ln.rect) and ln not in barriers
              and (ln.figure_font or ln.first_size < CAPTION_SIZE)]
    while True:
        grown = chain(region, pool, labels, blocked)
        # 라벨(⇩, × 같은 기호 포함)을 더한 뒤 다시 잇는다: 라벨을 사이에 두고 나란히 놓인 하위 그림을 잇기 위함이다
        region = absorb_labels(grown, lines, in_span, cap.rect.y0, rules)
        if region == grown:
            return region


def chain(region: pymupdf.Rect, pool, labels, blocked) -> pymupdf.Rect:
    """region에서 CHAIN_GAP·SIDE_GAP 안의 도형을 더 이을 것이 없을 때까지 잇는다."""
    region = pymupdf.Rect(region)
    changed = True
    while changed:
        changed = False
        top = region.y0
        for t in sorted((t for t in labels if t.y1 <= region.y0 + 1), key=lambda t: -t.y1):
            if t.y1 >= top - CHAIN_GAP and x_overlap(t, region.x0 - SIDE_GAP, region.x1 + SIDE_GAP) > 0:
                top = min(top, t.y0)
        for r in pool:
            if region.contains(r):
                continue
            near = (r.y1 >= top - CHAIN_GAP and r.y0 <= region.y1
                    and r.x0 <= region.x1 + SIDE_GAP and r.x1 >= region.x0 - SIDE_GAP)
            if near and not (r.y1 < region.y0 and blocked(r.y1, region.y0, r)):
                region |= r
                changed = True
    return region


def absorb_labels(region: pymupdf.Rect, lines: list[Line], in_span, limit: float, rules=()) -> pymupdf.Rect:
    """영역 안의 글자, 영역 옆·위·아래에 붙은 짧은 라벨, 영역 아래 limit까지의 소제목([회로], 그림 (a) 등)을 더한다.

    rules(표 칸선)를 사이에 둔 글자는 다른 칸의 글자라 더하지 않는다.
    """
    region = pymupdf.Rect(region)
    changed = True
    while changed:
        changed = False
        for ln in lines:
            r = ln.rect
            if region.contains(r) or not in_span(r) or r.y0 >= limit - 0.5:
                continue
            below = region.y1 - 1 <= r.y0 and r.y1 <= limit and x_overlap(r, region.x0, region.x1) > 0
            if is_body_line(ln) and not (below and ln.first_size < CAPTION_SIZE):
                continue
            # 본문 줄과 같은 높이에 나란히 놓인 짧은 줄(본문 속 수식 등)은 라벨이 아니다
            if any(b is not ln and is_body_line(b) and b.first_size >= CAPTION_SIZE and min(b.rect.y1, r.y1) - max(b.rect.y0, r.y0) > 0.5 * r.height
                   and (b.rect.x0 - 20 <= r.x1 and r.x0 <= b.rect.x1 + 20) for b in lines):
                continue
            if any(separates(t, r, region) for t in rules):
                continue
            # 그림 글꼴·작은 글자·한글 짧은 줄(범례 등)은 그림 라벨로 본다. 한글 없는 본문 크기 줄(v(t) 같은 수식 라벨)은
            # 그림 옆 본문 수식일 수도 있어서 그림에 바짝 붙어 있을 때만 더한다
            labelish = (ln.figure_font or ln.first_size < CAPTION_SIZE or re.search(r"[가-힣]", ln.text) is not None
                        or ARROWS_RE.fullmatch(ln.text) is not None)
            short = len(ln.text) <= LABEL_MAX_CHARS or ln.figure_font
            side, up = (LABEL_GAP * 3, LABEL_GAP) if labelish else (LABEL_GAP, TIGHT_GAP)
            inside = region.contains(r.mid)
            beside = (r.y1 > region.y0 and r.y0 < region.y1
                      and (region.x0 - side <= r.x1 and r.x0 <= region.x1 + side))
            above = region.y0 - up <= r.y1 <= region.y0 + 1 and x_overlap(r, region.x0, region.x1) > 0
            if inside or (short or ln.first_size < CAPTION_SIZE) and (beside or above or below):
                region |= r
                changed = True
    return region


def separates(t: pymupdf.Rect, a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    """칸선 t가 a와 b 사이를 가로막는가 (가로선은 위아래 사이, 세로선은 좌우 사이)."""
    if t.height < 2:
        return (min(a.y1, b.y1) - 1 <= t.y0 and t.y1 <= max(a.y0, b.y0) + 1
                and x_overlap(t, min(a.x0, b.x0), max(a.x1, b.x1)) > 0)
    return (min(a.x1, b.x1) - 1 <= t.x0 and t.x1 <= max(a.x0, b.x0) + 1
            and min(t.y1, max(a.y1, b.y1)) - max(t.y0, min(a.y0, b.y0)) > 0)


def is_grid(d: dict | None) -> bool:
    """표 칸선·칸 칠: 가로·세로 선과 사각형만으로 된 도형."""
    return d is not None and not is_drawing(d, pymupdf.Rect(d["rect"]))


def find_table(label: Line, graphics, area: pymupdf.Rect) -> pymupdf.Rect | None:
    """「표 N-M」 표시 바로 아래의 위 테두리에서 시작해, 표 너비의 가로선과 이어진 세로선·칸 칠로 아래로 넓힌 영역.

    표시가 표 아래 오른쪽에 붙은 교재(전기설비기술기준 일부)도 있다. 그때는 바로 위의 아래 테두리에서 위로 넓힌다.
    칸 안의 그림(파형, 소자 기호)은 영역을 넓히지 않는다. 표 바로 밑의 다른 그림이 딸려 들어오지 않게 하기 위함이다.
    """
    grid = [r for r, d in graphics if is_grid(d)]
    down = True
    edges = [r for r in grid if label.rect.y1 - 3 <= r.y0 <= label.rect.y1 + 14 and r.x0 <= label.rect.x1 + 5 and r.width > 60]
    if not edges:
        down = False
        edges = [r for r in grid if label.rect.y0 - 14 <= r.y1 <= label.rect.y0 + 3
                 and r.x1 >= label.rect.x0 - 5 and r.width > 60]
    if not edges:
        return None
    region = pymupdf.Rect(edges[0])
    for r in edges[1:]:
        region |= r
    width = region.width
    changed = True
    while changed:
        changed = False
        for r in grid:
            if region.contains(r) or r.x0 < region.x0 - 3 or r.x1 > region.x1 + 3:
                continue
            if down:
                row_line = r.width >= 0.8 * width and region.y1 - 1 <= r.y0 <= region.y1 + TABLE_ROW
                touching = region.y0 - 1 <= r.y0 <= region.y1 + 2
            else:
                row_line = r.width >= 0.8 * width and region.y0 - TABLE_ROW <= r.y1 <= region.y0 + 1
                touching = region.y0 - 2 <= r.y1 <= region.y1 + 1
            if row_line or touching:
                region |= r
                changed = True
    return region & area


def split_x(a: Line, b: Line, graphics, lines: list[Line]) -> float:
    """나란히 놓인 두 캡션의 그림 경계: 두 캡션 가운데 사이에서, 캡션 위의 도형·라벨이 없는 가장 넓은 세로 틈의 가운데."""
    x0, x1 = a.rect.mid.x, b.rect.mid.x
    top = a.rect.y0 - 300
    spans = sorted((max(r.x0, x0), min(r.x1, x1)) for r in
                   [g for g, _ in graphics] + [ln.rect for ln in lines if not is_body_line(ln) and ln is not a and ln is not b]
                   if top <= r.y1 <= a.rect.y0 + 1 and r.x1 > x0 and r.x0 < x1)
    best, edge = (0.0, (a.rect.x1 + b.rect.x0) / 2), x0
    for s0, s1 in spans:
        if s0 - edge > best[0]:
            best = (s0 - edge, (edge + s0) / 2)
        edge = max(edge, s1)
    if x1 - edge > best[0]:
        best = (x1 - edge, (edge + x1) / 2)
    return best[1]


def merge_while(rects: list[pymupdf.Rect], near) -> list[pymupdf.Rect]:
    """near(a, b)인 사각형끼리 더 합칠 것이 없을 때까지 합친다."""
    out = [pymupdf.Rect(r) for r in rects]
    i = 0
    while i < len(out):
        j = next((j for j in range(len(out)) if j != i and near(out[i], out[j])), None)
        if j is None:
            i += 1
            continue
        out[i] |= out[j]
        del out[j]
        i = 0
    return out


def cluster(rects: list[pymupdf.Rect], gap: float) -> list[pymupdf.Rect]:
    return merge_while(rects, lambda a, b: (a + (-gap, -gap, gap, gap)).intersects(b))


def merge_rows(rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """같은 높이에 나란히 놓인 덩어리를 합친다 (⇒ 글자를 사이에 둔 변환 전·후 회로 등)."""
    def near(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
        overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
        gap = max(a.x0, b.x0) - min(a.x1, b.x1)
        return overlap > 0.5 * min(a.height, b.height) and gap <= SIDE_GAP
    return merge_while(rects, near)


def is_badge(c: pymupdf.Rect, words: list[pymupdf.Rect], boxes: list[pymupdf.Rect]) -> bool:
    """「TIP」「STEP1」 같은 작은 표지: 낮고 좁은 덩어리를 낱말이 대부분 채우거나, 글 상자 위 테두리에 걸려 있다."""
    if c.height > 25 or c.width > 90:
        return False
    words = sum(w.width for w in words if c.contains(w.mid))
    on_box = any(b.y0 - 8 <= c.y1 <= b.y0 + 12 and x_overlap(c, b.x0, b.x1) > 0 for b in boxes)
    return words >= 0.4 * c.width or on_box


def load_manual(subject: str, number: int) -> dict:
    if not MANUAL.exists():
        return {}
    return json.loads(MANUAL.read_text(encoding="utf-8")).get(subject, {}).get(f"{number:02d}", {})


def analyze(pdf: Path) -> tuple[Chapter, pymupdf.Document]:
    book, number = parse_name(pdf)
    if book not in SUBJECTS:
        raise ValueError(f"tools/subjects.json에 없는 과목: {book}")
    ch = Chapter(pdf, book, SUBJECTS[book], number)
    doc = pymupdf.open(pdf)
    exams = exam_pages(doc)
    seen: dict[str, int] = {}
    for page in doc:
        text = page.get_text()
        if page.number in exams or BODY_FOOTER not in text or LECTURE_MARK in text:
            continue
        ch.body_pages.append(page.number)
        lines = page_lines(page)
        raw = page_graphics(page)
        graphics = [(r, d) for r, d in raw if not (is_text_box(d, r, lines) or is_fraction_bar(r, lines)
                                                   or is_decoration(r, lines) or is_formula_mark(d, r, lines))]
        graphics = drop_isolated_text_rects(graphics, lines)
        caps, tables = [], []
        for ln in lines:
            m = CAPTION_RE.match(ln.text)
            if m and ln.first_size < BODY_TEXT_SIZE:
                caps.append((ln, f"{m.group(1)}-{m.group(2)}", clean_title(m.group(3))))
            m = TABLE_RE.match(ln.text)
            if m:
                tables.append((ln, f"{m.group(1)}-{m.group(2)}"))
        # 그림 영역의 경계: 본문 줄(작은 글자의 소제목·범례 줄은 빼고), 캡션, 표 표시
        barriers = [ln for ln in lines if is_body_line(ln) and ln.first_size >= CAPTION_SIZE]
        barriers += [c[0] for c in caps] + [t[0] for t in tables]
        area = body_area(page)
        # 잘라 낼 때는 오른쪽 장 번호 탭 자리까지 허용한다 (탭은 쪽 위쪽 한 곳뿐이고, 그림 라벨이 그 줄까지 나온다)
        crop_area = pymupdf.Rect(area.x0, area.y0, page.rect.width - CROP_SIDE, area.y1)
        used: list[pymupdf.Rect] = []

        def add(f: Figure) -> None:
            if f.id in seen:
                ch.warnings.append(f"{f.label}: 두 번 나온다 (PDF p.{seen[f.id] + 1}, p.{page.number + 1})")
                return
            seen[f.id] = page.number
            used.append(f.rect)
            (ch.candidates if f.kind == "text-table" else ch.figures).append(f)

        # 표를 먼저 찾는다: 표의 칸선과 칸 안 글자가 바로 옆 그림에 딸려 들어가지 않게 하기 위함이다
        found = []
        for label, num in tables:
            region = find_table(label, raw, area)
            if region is None:
                ch.warnings.append(f"표 {num} (PDF p.{page.number + 1}): 표시 아래에서 표를 찾지 못했다")
                continue
            found.append((num, region, label))
        in_table = lambda r: any((t + (-1, -1, 1, 1)).contains(r) for _, t, _ in found)
        all_graphics = graphics
        graphics = [(r, d) for r, d in graphics if not in_table(r)]
        fig_lines = [ln for ln in lines if not in_table(ln.rect)]
        covered: set[str] = set()
        # 표 칸 안의 그림을 찾을 때는 표의 칸선·칸 칠(표 크기에 견줄 만큼 긴 것)을 뺀다. 그림의 선은 그보다 짧다
        def table_rule(r: pymupdf.Rect, d) -> bool:
            t = next((t for _, t, _ in found if (t + (-1, -1, 1, 1)).contains(r)), None)
            return t is not None and is_grid(d) and (r.width >= TABLE_RULE * t.width or r.height >= TABLE_RULE * t.height)
        cell_graphics = [(r, d) for r, d in all_graphics if not table_rule(r, d)]
        # 칸 안에서는 칸 글자(수식 등)가 아니라 표의 가로 칸선이 그림의 경계다
        # (표 아래 테두리는 빼서, 표 바로 아래 붙은 캡션에서 칸 안으로 올라갈 수 있게 한다.)
        # 칸 글자는 길어도 그림의 일부(라벨)로 본다
        cell_barriers = [c[0] for c in caps] + [Line(r, 0, "", 0) for r, d in all_graphics
                                                 if table_rule(r, d) and r.height < 2
                                                 and not any(abs(r.y1 - t.y1) <= 2 for _, t, _ in found)]
        rules = [r for r, d in all_graphics if table_rule(r, d) and min(r.width, r.height) < 2]
        cell_lines = [Line(ln.rect, ln.size, ln.text, ln.first_size, True) if in_table(ln.rect) else ln for ln in lines]
        for cap, num, title in caps:
            row = sorted((c for c in caps if abs(c[0].rect.y0 - cap.rect.y0) < 6), key=lambda c: c[0].rect.x0)
            i = next(k for k, c in enumerate(row) if c[0] is cap)
            left = area.x0 if i == 0 else split_x(row[i - 1][0], cap, graphics, fig_lines)
            right = area.x1 if i == len(row) - 1 else split_x(cap, row[i + 1][0], graphics, fig_lines)
            # 표 칸 안에 캡션이 있는 그림(표로 늘어놓은 그림)은 표의 도형·글자에서 찾는다
            inside = in_table(cap.rect)
            if inside:
                region = find_region(cap, (left, right), cell_graphics, cell_lines, cell_barriers, rules)
                if region is not None:
                    covered.update(n for n, t, _ in found if (t + (-1, -1, 1, 1)).contains(cap.rect))
            else:
                region = find_region(cap, (left, right), graphics, fig_lines, barriers)
            if region is None and not inside:
                # 캡션이 표 바로 아래에 붙은 그림: 표 칸에 늘어놓은 그림이다. 표는 이 그림들이 대신하므로 필수에서 뺀다 (칸 안 캡션도 같다)
                region = find_region(cap, (left, right), cell_graphics, cell_lines, cell_barriers, rules)
                if region is not None:
                    covered.update(n for n, t, _ in found if t.intersects(region))
            if region is None:
                ch.warnings.append(f"그림 {num} (PDF p.{page.number + 1}): 캡션 위에서 도형을 찾지 못했다")
                continue
            rect = (region + (-PAD, -PAD, PAD, PAD)) & crop_area
            rect.y1 = min(rect.y1, cap.rect.y0 - 0.5)  # 캡션은 노트가 따로 쓴다
            add(Figure(f"fig{num}", "figure", page.number, rect, title))
        for num, region, label in found:
            # 머리칸 대각선 하나(선 하나짜리 도형)는 그림으로 치지 않는다
            drawn = any(is_drawing(d, r) and region.contains(r) and not (d and len(d["items"]) == 1 and d["items"][0][0] == "l")
                        for r, d in all_graphics) and num not in covered
            rect = (region + (-PAD, -PAD, PAD, PAD)) & crop_area
            if label.rect.y1 <= region.y0 + 1:
                rect.y0 = max(rect.y0, label.rect.y1 + 0.5)  # 「표 N-M」 표시는 노트가 따로 쓴다
            else:
                rect.y1 = min(rect.y1, label.rect.y0 - 0.5)
            add(Figure(f"tab{num}", "table" if drawn else "text-table", page.number, rect))
        # 캡션 없는 도형 덩어리 → 캡션 없는 그림 (필수, 그림이 아니면 manual.json exclude로 뺀다)
        rest = [r for r, _ in graphics if not any(u.intersects(r) for u in used)]
        k = 0
        for c in sorted(merge_rows(cluster(rest, 6)), key=lambda c: (c.y0, c.x0)):
            if max(c.width, c.height) < UNCAPTIONED_MIN[0] or min(c.width, c.height) < UNCAPTIONED_MIN[1]:
                continue
            members = [(r, d) for r, d in graphics if c.contains(r)]
            if any(is_body_line(ln) and c.contains(ln.rect.mid) for ln in lines):
                # 본문 줄을 담은 덩어리: 표 너비의 가로 칸선이 있으면 앞 쪽에서 이어진 표시 없는 표, 아니면 글 상자(TIP 등)
                rows = [r for r, d in members if is_grid(d) and r.height < 2 and r.width >= 0.8 * c.width]
                if len(rows) < 2:
                    continue
                k += 1
                drawn = any(is_drawing(d, r) and not (d and len(d["items"]) == 1) for r, d in members)
                add(Figure(f"p{page.number + 1:02d}-{k}", "table" if drawn else "text-table", page.number,
                           (c + (-PAD, -PAD, PAD, PAD)) & crop_area))
                continue
            words = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]
            if is_badge(c, words, [r for r, d in raw if is_text_box(d, r, lines)]):
                continue
            region = absorb_labels(c, fig_lines, lambda r: True, c.y1 + LABEL_GAP * 2)
            if any(region.intersects(u) for u in used):
                region = c
            k += 1
            # 칸선만 있고 그림 글꼴 글자가 없는 덩어리는 표시 없는 표(루스 표 등)다 → 그림 없는 표 후보
            plain = all(is_grid(d) for _, d in members) and not any(
                ln.figure_font and region.contains(ln.rect.mid) for ln in fig_lines)
            add(Figure(f"p{page.number + 1:02d}-{k}", "text-table" if plain else "uncaptioned", page.number,
                       (region + (-PAD, -PAD, PAD, PAD)) & crop_area))
    apply_manual(ch, doc)
    nums = sorted(tuple(map(int, f.id[3:].split("-"))) for f in ch.figures if f.kind == "figure")
    for (a, b), (c, d) in zip(nums, nums[1:]):
        if a == c and d != b + 1:
            ch.warnings.append(f"그림 번호가 건너뛴다: {a}-{b} 다음 {c}-{d}")
    if nums and nums[0][1] != 1:
        ch.warnings.append(f"그림 번호가 1로 시작하지 않는다: {nums[0][0]}-{nums[0][1]}")
    other = {a for a, _ in nums} - {number}
    if other:
        ch.warnings.append(f"장 번호가 다른 그림이 있다: {sorted(other)}장")
    return ch, doc


def apply_manual(ch: Chapter, doc: pymupdf.Document) -> None:
    """manual.json: exclude(필수에서 빼기), merge(갈라진 상자 합치기), rect(영역 고치기), add(직접 지정 — 후보 id 또는 쪽·영역)."""
    manual = load_manual(ch.subject, ch.number)
    by_id = {f.id: f for f in ch.figures + ch.candidates}
    for fid in manual.get("exclude", []):
        if fid not in by_id or by_id[fid] not in ch.figures:
            ch.warnings.append(f"manual.json exclude: 필수 그림에 {fid}가 없다")
            continue
        ch.figures.remove(by_id[fid])
    for fid, parts in manual.get("merge", {}).items():
        # 한 그림이 여러 상자로 갈라진 것: 나머지 상자를 fid 영역에 합치고 필수에서 뺀다
        if fid not in by_id or any(p not in by_id for p in parts):
            ch.warnings.append(f"manual.json merge: {fid} 또는 {parts} 중 없는 것이 있다")
            continue
        for p in parts:
            by_id[fid].rect |= by_id[p].rect
            if by_id[p] in ch.figures:
                ch.figures.remove(by_id[p])
    for fid, rect in manual.get("rect", {}).items():
        if fid not in by_id:
            ch.warnings.append(f"manual.json rect: {fid}가 없다")
            continue
        by_id[fid].rect = pymupdf.Rect(rect)
    for entry in manual.get("add", []):
        if "from" in entry:
            src = by_id.get(entry["from"])
            if src is None or src not in ch.candidates:
                ch.warnings.append(f"manual.json add: 그림 없는 표 {entry['from']}가 없다")
                continue
            page, rect = src.page, src.rect
        else:
            page, rect = entry["page"] - 1, pymupdf.Rect(entry["rect"])
        if not 0 <= page < len(doc):
            ch.warnings.append(f"manual.json add: {entry['id']}의 쪽 번호가 범위 밖이다")
            continue
        if any(f.id == entry["id"] for f in ch.figures):
            ch.warnings.append(f"manual.json add: {entry['id']}가 이미 있다")
            continue
        ch.figures.append(Figure(entry["id"], "manual", page, rect, entry.get("title", "")))
        ch.candidates = [c for c in ch.candidates if not (c.page == page and c.rect.intersects(rect))]
    ch.figures.sort(key=lambda f: (f.page, f.rect.y0, f.rect.x0))


def check_private(page: pymupdf.Page, f: Figure) -> None:
    text = page.get_text(clip=f.rect)
    if PRIVATE_RE.search(text):
        raise RuntimeError(f"{f.id}: 잘라낼 영역에 워터마크·응원 문구로 보이는 글자가 있어 멈춘다")
    if f.rect.y0 < BODY_TOP - 1:
        raise RuntimeError(f"{f.id}: 잘라낼 영역이 머리말(응원 문구 자리)에 걸쳐 멈춘다")
    for block in page.get_text("dict", clip=f.rect)["blocks"]:
        for line in block.get("lines", []):
            dx, dy = line["dir"]
            if abs(dy) > 0.3 and pymupdf.Rect(line["bbox"]).width > 100:
                raise RuntimeError(f"{f.id}: 잘라낼 영역에 대각선 워터마크로 보이는 글자가 있어 멈춘다")


def render(page: pymupdf.Page, rect: pymupdf.Rect, path: Path) -> None:
    pix = page.get_pixmap(dpi=DPI, clip=rect, alpha=False)
    pix.set_dpi(DPI, DPI)
    pix.save(path)


COLORS = {  # 점검 이미지의 상자 색
    "figure": (0.9, 0.1, 0.1),
    "table": (0.0, 0.6, 0.2),
    "manual": (0.8, 0.0, 0.8),
    "uncaptioned": (0.1, 0.3, 0.95),
    "text-table": (0.55, 0.55, 0.55),
}


def write_audit(ch: Chapter, doc: pymupdf.Document) -> list[Path]:
    """쪽마다 찾은 것을 겹쳐 그리고, 네 쪽씩 한 장으로 묶은 점검 이미지 (빨강 캡션 그림, 초록 그림 든 표, 파랑 캡션 없는 그림, 보라 직접 지정, 회색 그림 없는 표)."""
    ch.audit_dir.mkdir(parents=True, exist_ok=True)
    for old in ch.audit_dir.glob("*.png"):
        old.unlink()
    shots = []
    for pno in ch.body_pages:
        page = doc[pno]
        # 머리말 줄(맨 위 응원 문구 포함)은 점검 이미지에서도 가린다
        page.draw_rect(pymupdf.Rect(0, 0, page.rect.width, BODY_TOP - 8), color=None, fill=(0.85, 0.85, 0.85))
        shape = page.new_shape()
        for f in ch.figures + ch.candidates:
            if f.page != pno:
                continue
            shape.draw_rect(f.rect)
            shape.finish(color=COLORS[f.kind], width=1.6)
            shape.insert_text(f.rect.tl + (2, 9), f.id, fontsize=9, color=COLORS[f.kind])
        shape.commit()
        shape = page.new_shape()
        shape.insert_text((8, 20), f"PDF p.{pno + 1}", fontsize=14, color=(0, 0, 0))
        shape.commit()
        shots.append(page.get_pixmap(dpi=75))
    sheets = []
    for k in range(0, len(shots), 4):
        group = shots[k:k + 4]
        w, h = group[0].width, group[0].height
        sheet = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w * 2, h * 2), False)
        sheet.clear_with(255)
        for i, pix in enumerate(group):
            pix.set_origin((i % 2) * w, (i // 2) * h)
            sheet.copy(pix, pix.irect)
        path = ch.audit_dir / f"sheet-{k // 4 + 1}.png"
        sheet.save(path)
        sheets.append(path)
    return sheets


def write_grid(pdf: Path, pno: int) -> Path:
    """manual.json에 적을 좌표를 읽기 위한 쪽 이미지: 20pt 간격 눈금과 지금 찾은 상자를 겹친다."""
    ch, doc = analyze(pdf)
    page = doc[pno]
    page.draw_rect(pymupdf.Rect(0, 0, page.rect.width, BODY_TOP - 8), color=None, fill=(0.85, 0.85, 0.85))
    shape = page.new_shape()
    for v in range(0, int(max(page.rect.width, page.rect.height)) + 1, 20):
        strong = v % 100 == 0
        if v <= page.rect.width:
            shape.draw_line((v, 0), (v, page.rect.height))
            shape.finish(color=(0, 0.5, 1) if strong else (0.6, 0.85, 1), width=0.4 if strong else 0.2)
            shape.insert_text((v + 1, BODY_TOP - 10), str(v), fontsize=5, color=(0, 0.3, 0.8))
        if v <= page.rect.height:
            shape.draw_line((0, v), (page.rect.width, v))
            shape.finish(color=(0, 0.5, 1) if strong else (0.6, 0.85, 1), width=0.4 if strong else 0.2)
            shape.insert_text((1, v - 1), str(v), fontsize=5, color=(0, 0.3, 0.8))
    for f in ch.figures + ch.candidates:
        if f.page == pno:
            shape.draw_rect(f.rect)
            shape.finish(color=COLORS[f.kind], width=1)
            shape.insert_text(f.rect.tl + (2, 7), f.id, fontsize=7, color=COLORS[f.kind])
    shape.commit()
    ch.audit_dir.mkdir(parents=True, exist_ok=True)
    path = ch.audit_dir / f"grid-p{pno + 1:02d}.png"
    page.get_pixmap(dpi=150).save(path)
    return path


def extract(pdf: Path, audit: bool) -> bool:
    ch, doc = analyze(pdf)
    for f in ch.figures:
        check_private(doc[f.page], f)
    ch.out_dir.mkdir(parents=True, exist_ok=True)
    for old in ch.out_dir.glob("*.png"):
        old.unlink()
    for f in ch.figures:
        render(doc[f.page], f.rect, ch.out_dir / f"{f.id}.png")
    item = lambda f: {"id": f.id, "kind": f.kind, "label": f.label, "page": f.page + 1, "title": f.title,
                      "rect": [round(v, 1) for v in f.rect]}
    manifest = {
        "pdf": unicodedata.normalize("NFC", pdf.name),
        "dpi": DPI,
        "figures": [item(f) | {"file": f"{f.id}.png"} for f in ch.figures],
        "candidates": [item(f) for f in ch.candidates],
        "warnings": ch.warnings,
    }
    (ch.out_dir / "figures.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {k: sum(f.kind == k for f in ch.figures + ch.candidates) for k in COLORS}
    print(f"{ch.book} {ch.number}장 → {rel(ch.out_dir)}/: 필수 {len(ch.figures)}개"
          f" (캡션 그림 {counts['figure']}, 그림 든 표 {counts['table']}, 캡션 없는 그림 {counts['uncaptioned']},"
          f" 직접 지정 {counts['manual']}) | 그림 없는 표 {counts['text-table']}개")
    if audit:
        sheets = write_audit(ch, pymupdf.open(pdf))
        print(f"  점검 이미지 {len(sheets)}장: {rel(ch.audit_dir)}/sheet-*.png")
    for w in ch.warnings:
        print(f"  경고: {w}")
    return not ch.warnings


IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")


def check(note: Path) -> bool:
    """챕터 노트(모든 파트)가 필수 그림을 빠짐없이, 한 번씩만 넣었는지 확인한다."""
    note = note.resolve()
    m = re.match(r"(ch(\d+)-.+?)(?:-\d+)?\.md$", note.name)
    if not m:
        print(f"노트 파일명 형식이 아니다: {note.name}")
        return False
    prefix, number = m.group(1), int(m.group(2))
    parts = sorted(p for p in note.parent.glob("ch*.md") if re.fullmatch(re.escape(prefix) + r"(-\d+)?\.md", p.name))
    folder = f"figures/ch{number:02d}/"
    manifest_path = note.parent / folder / "figures.json"
    if not manifest_path.exists():
        print(f"{rel(manifest_path)} 가 없다. 먼저 교재 챕터 PDF로 이 도구를 실행한다.")
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {folder + f["file"]: f["label"] if f["kind"] != "manual" else f["id"] for f in manifest["figures"]}
    used: dict[str, list[str]] = {}
    ok = True
    for p in parts:
        for src in IMAGE_RE.findall(p.read_text(encoding="utf-8")):
            key = src.removeprefix("./")
            used.setdefault(key, []).append(p.name)
            if not (p.parent / key).exists():
                print(f"  없는 그림 파일: {p.name} → {src}")
                ok = False
            elif key.startswith(folder) and key not in expected:
                print(f"  필수 목록(figures.json)에 없는 그림: {p.name} → {src}")
                ok = False
    for key, label in expected.items():
        n = len(used.get(key, []))
        if n == 0:
            print(f"  빠진 그림: {label} ({key})")
            ok = False
        elif n > 1:
            print(f"  두 번 넣은 그림: {label} ({', '.join(used[key])})")
            ok = False
    if manifest["warnings"]:
        print(f"  그림 추출 경고가 남아 있다: {len(manifest['warnings'])}건 (도구를 다시 실행해 확인)")
        ok = False
    print(f"{prefix}: 필수 그림 {len(expected)}개 중 {sum(1 for k in expected if k in used)}개를 넣었다"
          + (" — 통과" if ok else " — 실패"))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, help="교재 챕터 PDF 또는 폴더 (--check면 챕터 노트 파일)")
    ap.add_argument("--out", type=Path, help="그림을 넣을 노트 폴더 (기본 notes/, 아래에 {과목 폴더}/figures/chNN/)")
    ap.add_argument("--work", type=Path, help="점검 이미지 폴더 (기본 .cache/work/, 아래에 {과목 폴더}/chNN-figures/)")
    ap.add_argument("--no-audit", action="store_true", help="점검 이미지를 만들지 않는다")
    ap.add_argument("--check", action="store_true", help="노트가 챕터의 필수 그림을 빠짐없이 넣었는지 확인한다")
    ap.add_argument("--grid", type=int, metavar="쪽", help="이 PDF 쪽에 좌표 눈금을 겹친 이미지를 점검 폴더에 만든다 (manual.json 좌표 읽기용)")
    args = ap.parse_args()
    global NOTES, WORK
    NOTES = args.out.resolve() if args.out else NOTES
    WORK = args.work.resolve() if args.work else WORK
    if args.check:
        return 0 if all([check(p) for p in args.paths]) else 1
    if args.grid:
        for p in args.paths:
            print(rel(write_grid(p, args.grid - 1)))
        return 0
    pdfs = []
    for p in args.paths:
        pdfs += sorted(p.glob("*_제*장_*.pdf")) if p.is_dir() else [p]
    ok = True
    for pdf in pdfs:
        try:
            ok &= extract(pdf, not args.no_audit)
        except (ValueError, RuntimeError) as e:
            print(f"{pdf.name}: {e}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
