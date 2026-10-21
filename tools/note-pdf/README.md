# note-pdf — 필기 여백이 넓은 챕터 PDF

챕터마다 PDF 한 권을 만든다. 아이패드 굿노트에서 필기하며 공부하기 위한 것이다.

1. 노트 본문: 챕터의 파트(`ch03-…-1.md`, `ch03-…-2.md`, …)를 이어서
2. 기출 문제지: [exam-sheets](../exam-sheets/README.md)가 만든 `chNN-questions.pdf`
3. 기출 정답·해설: 같은 곳의 `chNN-answers.pdf` (`--with-answers`를 줄 때만)

모든 페이지는 본문을 보통 A4 한 장처럼 배치하고, 그 오른쪽에 필기 띠를 덧붙인 만큼 용지를 넓힌다 (기본 285 × 297mm = A4 + 75mm).
문제지·해설 페이지도 같은 크기로 맞추고, 오른쪽 띠는 풀이 공간으로 비워 둔다.
노트 렌더링은 VS Code 미리보기와 같은 계열(markdown-it + KaTeX)이고, PDF는 설치된 크롬으로 찍는다.

## 준비 (처음 한 번)

```bash
cd tools/note-pdf && PUPPETEER_SKIP_DOWNLOAD=1 npm install
```

기출 문제지·해설은 따로 만들 필요가 없다. 챕터의 문제지·해설 PDF가 아직 없으면 note-pdf가 교재 챕터 PDF(`textbook/<과목>/<과목>_제NN장_….pdf`)로 [exam-sheets](../exam-sheets/README.md)를 먼저 돌려 만든다. 그래서 [uv](https://docs.astral.sh/uv/)도 있어야 한다.
교재 과목 이름과 과목 폴더의 대응은 [`tools/subjects.json`](../subjects.json)에서 읽는다 (exam-sheets와 같은 파일).

## 사용

```bash
node tools/note-pdf/note_pdf.mjs notes/circuit-theory                          # 과목의 모든 챕터
node tools/note-pdf/note_pdf.mjs notes/circuit-theory/ch03-rlc-ac-circuit-1.md  # 이 파일이 속한 3장 한 권
```

결과는 `tools/note-pdf/output/<과목 폴더>/<챕터 이름>.pdf` 하나다 (예: `ch03-rlc-ac-circuit.pdf`, git 제외).

| 옵션 | 뜻 | 기본 |
|---|---|---|
| `--note <mm>` | 본문 용지 오른쪽에 덧붙일 필기 띠 너비 | 75 |
| `--margin <mm>` | 본문 용지의 위·아래·왼쪽·오른쪽 여백 | 12 |
| `--paper A4\|Letter` | 본문 용지 (실제 PDF 너비는 이 너비 + 필기 띠) | A4 |
| `--out <폴더>` | 저장 폴더 | `output/<과목 폴더>` |
| `--exam <폴더>` | 기출 문제지·해설 PDF 폴더. 주면 exam-sheets를 자동으로 돌리지 않는다 | `../exam-sheets/output/<과목 폴더>` |
| `--refresh-exam` | 문제지·해설이 이미 있어도 exam-sheets로 다시 만든다 (exam-sheets를 고쳤을 때) | 없을 때만 만듦 |
| `--with-answers` | 문제지 뒤에 정답·해설도 붙인다 | 문제지만 |
| `--no-exam` | 기출 문제지(와 해설)를 붙이지 않는다 | 붙임 |
| `--no-line` | 필기 띠 구분 점선을 빼기 | 점선 있음 |
| `--hide-answers` | 노트의 `<details>` 답을 접힌 채로 둔다 (PDF에서는 펼 수 없으니 답이 아예 안 보인다) | 펼쳐서 인쇄 |

- 책갈피(목차)에 노트의 파트·마디와 「기출 문제」(`--with-answers`면 「기출 정답·해설」도)가 들어간다. 굿노트의 목차에서 바로 이동할 수 있다.
- 노트 안 링크(`#마디`, 같은 챕터의 다른 파트)는 PDF 안에서 동작한다. 다른 챕터로 가는 링크는 그 챕터 PDF 파일을 가리킨다.
- `--with-answers`로 붙인 정답표의 "해설 위치 N쪽"은 해설 PDF 안의 쪽수라서, 묶은 PDF의 쪽 번호와는 다르다. 정답표 바로 다음 쪽부터 해설이다.
- 문제지·해설에는 교재 워터마크가 남아 있으므로 결과 PDF는 개인 학습용으로만 쓰고 공유하지 않는다.
- 크롬을 못 찾으면 `CHROME_PATH` 환경변수로 실행 파일 경로를 준다.
