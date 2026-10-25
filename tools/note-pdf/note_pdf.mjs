#!/usr/bin/env node
// 챕터 노트(파트 여러 개)와 그 챕터의 기출 문제지·정답 해설을 PDF 한 권으로 묶는다 (아이패드 굿노트 필기용).
// 모든 페이지는 본문 용지 오른쪽에 필기 띠를 덧붙인 크기다.
// 노트 렌더링은 VS Code 미리보기와 같은 계열(markdown-it + KaTeX)이고, PDF는 설치된 크롬으로 찍는다.
// 기출 문제지·해설은 tools/exam-sheets가 만든 PDF를 그대로 가져와 붙인다. 아직 없으면 exam-sheets를 먼저 돌린다.

import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync, statSync, readdirSync, mkdtempSync, mkdirSync, rmSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { basename, dirname, extname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs } from "node:util";
import MarkdownIt from "markdown-it";
import anchor from "markdown-it-anchor";
import katexPlugin from "@vscode/markdown-it-katex";
import puppeteer from "puppeteer-core";
import { PDFDocument, PDFName, PDFHexString, PDFNumber, rgb } from "pdf-lib";

const HERE = dirname(fileURLToPath(import.meta.url));
// CSS는 반드시 수식을 렌더링하는 플러그인이 실제로 쓰는 KaTeX의 것이어야 한다. 버전이 다르면 분수·근호 배치가 어긋난다.
const KATEX_CSS = createRequire(createRequire(import.meta.url).resolve("@vscode/markdown-it-katex"))
  .resolve("katex/dist/katex.min.css");
const ROOT = resolve(HERE, "../..");
const EXAM_TOOL = join(HERE, "../exam-sheets");
const EXAM_DIR = join(EXAM_TOOL, "output");
// 교재 과목 → 과목 폴더 (exam-sheets와 함께 쓴다)
const SUBJECTS = JSON.parse(readFileSync(join(HERE, "../subjects.json"), "utf8"));
const MM = 72 / 25.4; // 1mm [pt]
// 본문 용지 크기 [mm] (가로, 세로)
const PAPERS = { A4: [210, 297], Letter: [215.9, 279.4] };
const CHROME_PATHS = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
];

const USAGE = `사용법: node tools/note-pdf/note_pdf.mjs <노트.md 또는 과목 폴더>... [옵션]

챕터 단위로 PDF 한 권을 만든다: 노트 파트 전부 → 기출 문제지 (→ 정답·해설, --with-answers일 때).
노트 파일 하나를 주면 그 파일이 속한 챕터 전체를 만든다.

옵션 (길이 단위 mm):
  --out <폴더>      PDF를 저장할 폴더 (기본: tools/note-pdf/output/<과목 폴더>)
  --exam <폴더>     기출 문제지·해설 PDF가 있는 폴더. 주면 exam-sheets를 자동으로 돌리지 않는다
                    (기본: tools/exam-sheets/output/<과목 폴더>, 없으면 교재 PDF로 자동 생성)
  --refresh-exam    기출 문제지·해설이 이미 있어도 exam-sheets로 다시 만든다
  --with-answers    기출 문제지 뒤에 정답·해설도 붙인다
  --no-exam         기출 문제지(와 해설)를 붙이지 않는다
  --note <mm>       본문 용지 오른쪽에 덧붙일 필기 띠 너비 (기본 75)
  --margin <mm>     본문 용지의 위·아래·왼쪽·오른쪽 여백 (기본 12)
  --paper <이름>    본문 용지 A4 | Letter (기본 A4). 실제 PDF 용지는 이 너비 + 필기 띠
  --no-line         필기 띠 구분선을 그리지 않는다
  --hide-answers    <details> 답을 접힌 채로 둔다 (기본은 펼쳐서 인쇄)`;

const { values: opt, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    out: { type: "string" },
    exam: { type: "string" },
    "no-exam": { type: "boolean", default: false },
    "with-answers": { type: "boolean", default: false },
    "refresh-exam": { type: "boolean", default: false },
    note: { type: "string", default: "75" },
    margin: { type: "string", default: "12" },
    paper: { type: "string", default: "A4" },
    "no-line": { type: "boolean", default: false },
    "hide-answers": { type: "boolean", default: false },
    help: { type: "boolean", short: "h", default: false },
  },
});

if (!(opt.paper in PAPERS)) {
  console.error(`모르는 용지: ${opt.paper} (A4 | Letter)`);
  process.exit(1);
}

if (opt.help || positionals.length === 0) {
  console.log(USAGE);
  process.exit(opt.help ? 0 : 1);
}

const NOTE = Number(opt.note);
const [PAPER_W, PAPER_H] = PAPERS[opt.paper];

// 헤딩 id를 GitHub·VS Code 미리보기와 같은 규칙으로 만들어 노트 안 링크(#...)가 PDF에서도 동작하게 한다.
const slugify = (s) =>
  s.trim().toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, "").replace(/\s/g, "-");

// breaks: 노트 머리의 "이 파일 / 이전 / 다음" 줄처럼 줄바꿈 하나로 나눈 줄을 PDF에서도 따로 보여 준다.
const md = new MarkdownIt({ html: true, linkify: true, breaks: true })
  .use(katexPlugin.default ?? katexPlugin)
  .use(anchor, { slugify });

// 같은 권에 묶인 파트로 가는 링크는 권 안의 위치(#...)로, 다른 챕터 노트로 가는 링크는 그 챕터 PDF로 바꾼다.
const defaultLink = md.renderer.rules.link_open ?? ((t, i, o, e, self) => self.renderToken(t, i, o));
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const href = tokens[idx].attrGet("href");
  const m = href?.match(/^(?:\.\/)?([^/:#]+)\.md(#.*)?$/);
  if (m) {
    const [, name, hash] = m;
    if (env.parts.has(name)) tokens[idx].attrSet("href", hash ?? `#part-${name}`);
    else tokens[idx].attrSet("href", `./${chapterName(name)}.pdf${hash ?? ""}`);
  }
  return defaultLink(tokens, idx, options, env, self);
};

// 노트의 그림(교재 그림, tools/textbook-figures)은 노트 파일 기준 상대 경로라, HTML이 임시 폴더에 있어도 찾도록 절대 경로로 바꾼다.
// 크기는 PNG에 적힌 DPI로 교재에 인쇄된 크기를 되살린다 (본문 폭보다 크면 CSS가 줄인다).
md.renderer.rules.image = (tokens, idx, options, env, self) => {
  const token = tokens[idx];
  const src = token.attrGet("src");
  if (src && !/^[a-z]+:/i.test(src)) {
    const file = resolve(env.dir, decodeURI(src));
    token.attrSet("src", pathToFileURL(file).href);
    const size = pngSize(file);
    if (size) token.attrSet("style", `width: ${size.width.toFixed(1)}pt`);
  }
  return self.renderToken(tokens, idx, options);
};

// PNG 폭을 pt로: IHDR의 픽셀 폭과 pHYs의 해상도(없으면 300 DPI, textbook-figures 기본값)
function pngSize(file) {
  if (!existsSync(file) || extname(file).toLowerCase() !== ".png") return null;
  const buf = readFileSync(file);
  const px = buf.readUInt32BE(16);
  let dpi = 300;
  const at = buf.indexOf("pHYs");
  if (at > 0 && buf[at + 12] === 1) dpi = buf.readUInt32BE(at + 4) * 0.0254;
  return { width: (px * 72) / dpi };
}

// "ch03-rlc-ac-circuit-2" → "ch03-rlc-ac-circuit" (파트 순번을 뗀 챕터 이름)
const chapterName = (name) => name.replace(/-\d+$/, "");
const chapterNumber = (name) => name.match(/^ch(\d+)/)?.[1];

function css() {
  const line = opt["no-line"]
    ? ""
    : `.note-line { position: fixed; top: 0; bottom: 0; right: 0; border-left: 0.4pt dashed #c8c8c8; }`;
  return `
/* 본문은 원래 용지 그대로 배치하고(오른쪽 여백은 body padding), 그 오른쪽에 필기 띠를 덧붙인다. */
@page { size: ${PAPER_W + NOTE}mm ${PAPER_H}mm; margin: ${opt.margin}mm ${NOTE}mm ${opt.margin}mm ${opt.margin}mm; }
body { padding-right: ${opt.margin}mm; }
${line}
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; font-family: -apple-system, "Apple SD Gothic Neo", "Pretendard", sans-serif;
       font-size: 9.5pt; line-height: 1.6; color: #222; word-break: keep-all; overflow-wrap: break-word; }
.part + .part { break-before: page; }
h1 { font-size: 15pt; border-bottom: 1px solid #ddd; padding-bottom: .2em; }
h2 { font-size: 13pt; margin-top: 1.6em; break-after: avoid; }
h3 { font-size: 11pt; margin-top: 1.3em; break-after: avoid; }
h4 { font-size: 10pt; break-after: avoid; }
p, li { orphans: 2; widows: 2; }
a { color: #1a6fb5; text-decoration: none; }
blockquote { margin: .8em 0; padding: .3em .9em; border-left: 3px solid #d0d0d0;
             background: #f6f6f6; color: #444; break-inside: avoid; }
blockquote p { margin: .3em 0; }
table { border-collapse: collapse; width: 100%; font-size: 8.5pt; margin: .8em 0; break-inside: auto; }
th, td { border: 1px solid #ddd; padding: .3em .5em; vertical-align: top; }
th { background: #f3f3f3; }
tr { break-inside: avoid; }
code { font-family: Menlo, monospace; font-size: 8.5pt; background: #f3f3f3; padding: 0 .2em; border-radius: 3px; }
pre { background: #f6f6f6; padding: .6em; overflow: hidden; white-space: pre-wrap; break-inside: avoid; }
pre code { background: none; padding: 0; }
hr { border: 0; border-top: 1px solid #ddd; margin: 1.5em 0; }
.katex-display { margin: .7em 0; overflow: hidden; break-inside: avoid; }
img { display: block; max-width: 100%; height: auto; margin: .3em auto; }
p:has(> img) { text-align: center; font-size: 8.5pt; color: #555; break-inside: avoid; }
.katex { font-size: 1.08em; }
details { margin: .4em 0; }
summary { font-weight: 600; }
`;
}

// 파트들을 한 HTML 문서로 이어 붙인다. env를 공유해 파트 사이에 헤딩 id가 겹치지 않게 한다.
function toHtml(title, partPaths) {
  const env = { parts: new Set(partPaths.map((p) => basename(p, ".md"))) };
  let body = partPaths
    .map((p) => {
      env.dir = dirname(p);
      return `<section class="part" id="part-${basename(p, ".md")}">${md.render(readFileSync(p, "utf8"), env)}</section>`;
    })
    .join("\n");
  if (!opt["hide-answers"]) body = body.replace(/<details(?![^>]*\bopen\b)/g, "<details open");
  return `<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>${title}</title>
<link rel="stylesheet" href="${pathToFileURL(KATEX_CSS)}">
<style>${css()}</style></head>
<body><div class="note-line"></div>${body}</body></html>`;
}

// 입력을 챕터별로 묶는다: Map<"과목폴더/챕터이름", {subjectDir, name, parts[]}>
function collect(paths) {
  const chapters = new Map();
  const add = (dir, name) => {
    const ch = chapterName(name);
    const key = join(dir, ch);
    if (chapters.has(key)) return;
    const parts = readdirSync(dir)
      .filter((f) => f.endsWith(".md") && chapterName(basename(f, ".md")) === ch)
      .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
      .map((f) => join(dir, f));
    chapters.set(key, { subjectDir: dir, name: ch, parts });
  };
  for (const p of paths.map((x) => resolve(x))) {
    if (statSync(p).isDirectory()) {
      for (const f of readdirSync(p).filter((f) => /^ch\d+.*\.md$/.test(f)).sort()) add(p, basename(f, ".md"));
    } else if (extname(p) === ".md") add(dirname(p), basename(p, ".md"));
  }
  return [...chapters.values()];
}

// 기출 PDF 한 쪽을 노트와 같은 크기의 페이지 왼쪽(본문 용지 자리)에 맞춰 넣고, 오른쪽은 필기 띠로 비운다.
async function appendExam(doc, examPath) {
  const src = await PDFDocument.load(readFileSync(examPath));
  const embedded = await doc.embedPages(src.getPages());
  const pageW = (PAPER_W + NOTE) * MM;
  const pageH = PAPER_H * MM;
  const first = doc.getPageCount();
  for (const e of embedded) {
    const page = doc.addPage([pageW, pageH]);
    const scale = Math.min((PAPER_W * MM) / e.width, pageH / e.height);
    const w = e.width * scale;
    const h = e.height * scale;
    page.drawPage(e, { x: (PAPER_W * MM - w) / 2, y: pageH - h, width: w, height: h });
    if (!opt["no-line"]) {
      page.drawLine({
        start: { x: PAPER_W * MM, y: 0 }, end: { x: PAPER_W * MM, y: pageH },
        thickness: 0.4, color: rgb(0.78, 0.78, 0.78), dashArray: [3, 3],
      });
    }
  }
  return first;
}

// 크롬이 만든 책갈피(목차) 맨 뒤에 최상위 항목을 하나 붙인다. 굿노트 목차에서 문제지·해설로 바로 가기 위함이다.
function addBookmark(doc, title, pageIndex) {
  const ctx = doc.context;
  const catalog = doc.catalog;
  let outlinesRef = catalog.get(PDFName.of("Outlines"));
  let outlines = outlinesRef && ctx.lookup(outlinesRef);
  if (!outlines) {
    outlines = ctx.obj({ Type: "Outlines", Count: 0 });
    outlinesRef = ctx.register(outlines);
    catalog.set(PDFName.of("Outlines"), outlinesRef);
  }
  const item = ctx.obj({
    Title: PDFHexString.fromText(title),
    Parent: outlinesRef,
    Dest: [doc.getPage(pageIndex).ref, PDFName.of("XYZ"), null, null, null],
  });
  const itemRef = ctx.register(item);
  const lastRef = outlines.get(PDFName.of("Last"));
  if (lastRef) {
    ctx.lookup(lastRef).set(PDFName.of("Next"), itemRef);
    item.set(PDFName.of("Prev"), lastRef);
  } else {
    outlines.set(PDFName.of("First"), itemRef);
  }
  outlines.set(PDFName.of("Last"), itemRef);
  const count = outlines.get(PDFName.of("Count"));
  outlines.set(PDFName.of("Count"), PDFNumber.of((count?.asNumber?.() ?? 0) + 1));
}

// 붙일 기출 PDF 종류: 기본은 문제지만, --with-answers면 정답·해설까지
const examKinds = () => [["questions", "기출 문제"], ...(opt["with-answers"] ? [["answers", "기출 정답·해설"]] : [])];

// 챕터의 기출 문제지·해설이 없으면(또는 --refresh-exam이면) 교재 챕터 PDF로 exam-sheets를 돌려 만든다.
function ensureExam(subject, no, examDir) {
  const paths = examKinds().map(([k]) => k).map((k) => join(examDir, `ch${no}-${k}.pdf`));
  if (opt.exam || (paths.every(existsSync) && !opt["refresh-exam"])) return;
  const book = Object.keys(SUBJECTS).find((k) => SUBJECTS[k] === subject);
  const bookDir = book && join(ROOT, "textbook", book);
  const pdf = bookDir && existsSync(bookDir)
    && readdirSync(bookDir).find((f) => f.startsWith(`${book}_제${no}장_`) && f.endsWith(".pdf"));
  if (!pdf) {
    console.warn(`  경고: ${subject} ${no}장 교재 PDF를 찾지 못해 기출 문제지·해설을 만들지 못했다 (tools/subjects.json, textbook/ 확인)`);
    return;
  }
  console.log(`  exam-sheets 실행: ${pdf}`);
  const res = spawnSync("uv", ["run", "--quiet", "exam_sheets.py", join(bookDir, pdf)], { cwd: EXAM_TOOL, stdio: "inherit" });
  if (res.error) console.warn(`  경고: exam-sheets를 실행하지 못했다 (uv 설치 확인): ${res.error.message}`);
}

const chrome = process.env.CHROME_PATH ?? CHROME_PATHS.find(existsSync);
if (!chrome) {
  console.error("크롬을 찾지 못했다. CHROME_PATH 환경변수로 실행 파일 경로를 알려 준다.");
  process.exit(1);
}

const chapters = collect(positionals);
if (chapters.length === 0) {
  console.error("변환할 노트(ch*.md)가 없다.");
  process.exit(1);
}

const work = mkdtempSync(join(tmpdir(), "note-pdf-"));
const browser = await puppeteer.launch({ executablePath: chrome, headless: true });
try {
  const page = await browser.newPage();
  for (const ch of chapters) {
    const subject = basename(ch.subjectDir);
    const htmlPath = join(work, ch.name + ".html");
    writeFileSync(htmlPath, toHtml(ch.name, ch.parts));
    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: "networkidle0" });
    await page.evaluate(() => document.fonts.ready);
    const notePdf = await page.pdf({
      preferCSSPageSize: true,
      printBackground: true,
      outline: true,
      tagged: true,
      displayHeaderFooter: true,
      headerTemplate: "<span></span>",
      footerTemplate: `<div style="width:100%;font-size:7pt;color:#999;padding-left:${opt.margin}mm">
        <span class="pageNumber"></span></div>`,
    });

    // 노트 PDF를 그대로 두고 그 뒤에 페이지를 덧붙인다. 노트 안 링크와 책갈피가 깨지지 않게 하기 위함이다.
    const doc = await PDFDocument.load(notePdf);
    const added = [];
    if (!opt["no-exam"]) {
      const examDir = opt.exam ? resolve(opt.exam) : join(EXAM_DIR, subject);
      const no = chapterNumber(ch.name);
      ensureExam(subject, no, examDir);
      for (const [kind, title] of examKinds()) {
        const examPath = join(examDir, `ch${no}-${kind}.pdf`);
        if (!existsSync(examPath)) {
          console.warn(`  경고: ${examPath} 가 없어 ${title}을 붙이지 않았다`);
          continue;
        }
        addBookmark(doc, title, await appendExam(doc, examPath));
        added.push(title);
      }
    }

    const outDir = opt.out ? resolve(opt.out) : join(HERE, "output", subject);
    mkdirSync(outDir, { recursive: true });
    const outPath = join(outDir, ch.name + ".pdf");
    writeFileSync(outPath, await doc.save());
    const parts = ch.parts.map((p) => basename(p, ".md")).join(", ");
    console.log(`만듦: ${outPath} (${doc.getPageCount()}쪽: ${[parts, ...added].join(" → ")})`);
  }
} finally {
  await browser.close();
  rmSync(work, { recursive: true, force: true });
}
