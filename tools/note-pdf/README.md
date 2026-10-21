# note-pdf — 필기 여백이 넓은 노트 PDF

노트 마크다운(`notes/**.md`)을 아이패드 굿노트에서 필기하기 좋게, 오른쪽에 넓은 빈 여백을 둔 PDF로 만든다.
본문은 보통 A4 한 장처럼 배치하고, 그 오른쪽에 필기 띠를 덧붙인 만큼 용지를 넓힌다 (기본 285 × 297mm = A4 + 75mm).
렌더링은 VS Code 미리보기와 같은 계열(markdown-it + KaTeX)이고, PDF는 설치된 크롬으로 찍는다.

## 준비 (처음 한 번)

```bash
cd tools/note-pdf && PUPPETEER_SKIP_DOWNLOAD=1 npm install
```

## 사용

```bash
node tools/note-pdf/note_pdf.mjs notes/circuit-theory/ch03-rlc-ac-circuit-1.md
node tools/note-pdf/note_pdf.mjs notes/circuit-theory          # 폴더의 노트 전부 (_index.md 제외)
```

결과는 `tools/note-pdf/output/<과목 폴더>/`에 노트와 같은 이름의 `.pdf`로 생긴다 (git 제외).

| 옵션 | 뜻 | 기본 |
|---|---|---|
| `--note <mm>` | 본문 용지 오른쪽에 덧붙일 필기 띠 너비 | 75 |
| `--margin <mm>` | 본문 용지의 위·아래·왼쪽·오른쪽 여백 | 12 |
| `--paper A4\|Letter` | 본문 용지 (실제 PDF 너비는 이 너비 + 필기 띠) | A4 |
| `--out <폴더>` | 저장 폴더 | `output/<과목 폴더>` |
| `--no-line` | 필기 여백 구분 점선을 빼기 | 점선 있음 |
| `--hide-answers` | `<details>` 답을 접힌 채로 둔다 (PDF에서는 펼 수 없으니 답이 아예 안 보인다) | 펼쳐서 인쇄 |

- 노트 안 링크(`#마디`)는 PDF 안에서도 동작한다. 다른 노트로 가는 `.md` 링크는 같은 이름의 `.pdf`를 가리키게 바뀐다.
- 크롬을 못 찾으면 `CHROME_PATH` 환경변수로 실행 파일 경로를 준다.
