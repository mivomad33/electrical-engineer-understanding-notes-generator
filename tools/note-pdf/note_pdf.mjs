#!/usr/bin/env node
// 노트 마크다운을 오른쪽에 필기 여백이 넓은 PDF로 만든다 (아이패드 굿노트 필기용).
// 렌더링은 VS Code 미리보기와 같은 계열(markdown-it + KaTeX)이고, PDF는 설치된 크롬으로 찍는다.

import { readFileSync, writeFileSync, statSync, readdirSync, mkdtempSync, mkdirSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, extname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs } from "node:util";
import MarkdownIt from "markdown-it";
import anchor from "markdown-it-anchor";
import katexPlugin from "@vscode/markdown-it-katex";
import puppeteer from "puppeteer-core";

const HERE = dirname(fileURLToPath(import.meta.url));
const KATEX_CSS = join(HERE, "node_modules/katex/dist/katex.min.css");
// 본문 용지 크기 [mm] (가로, 세로)
const PAPERS = { A4: [210, 297], Letter: [215.9, 279.4] };
const CHROME_PATHS = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
];

const USAGE = `사용법: node tools/note-pdf/note_pdf.mjs <노트.md 또는 폴더>... [옵션]

옵션 (길이 단위 mm):
  --out <폴더>      PDF를 저장할 폴더 (기본: tools/note-pdf/output/<과목 폴더>)
  --note <mm>       본문 용지 오른쪽에 덧붙일 필기 띠 너비 (기본 75)
  --margin <mm>     본문 용지의 위·아래·왼쪽·오른쪽 여백 (기본 12)
  --paper <이름>    본문 용지 A4 | Letter (기본 A4). 실제 PDF 용지는 이 너비 + 필기 띠
  --no-line         필기 여백 구분선을 그리지 않는다
  --hide-answers    <details> 답을 접힌 채로 둔다 (기본은 펼쳐서 인쇄)`;

const { values: opt, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    out: { type: "string" },
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

// 헤딩 id를 GitHub·VS Code 미리보기와 같은 규칙으로 만들어 노트 안 링크(#...)가 PDF에서도 동작하게 한다.
const slugify = (s) =>
  s.trim().toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, "").replace(/\s/g, "-");

// breaks: 노트 머리의 "이 파일 / 이전 / 다음" 줄처럼 줄바꿈 하나로 나눈 줄을 PDF에서도 따로 보여 준다.
const md = new MarkdownIt({ html: true, linkify: true, breaks: true })
  .use(katexPlugin.default ?? katexPlugin)
  .use(anchor, { slugify });

// 다른 노트로 가는 링크(.md)는 같은 이름의 PDF를 가리키게 바꾼다.
const defaultLink = md.renderer.rules.link_open ?? ((t, i, o, e, self) => self.renderToken(t, i, o));
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const href = tokens[idx].attrGet("href");
  if (href && !/^[a-z]+:/i.test(href)) tokens[idx].attrSet("href", href.replace(/\.md(#|$)/, ".pdf$1"));
  return defaultLink(tokens, idx, options, env, self);
};

function css() {
  const note = Number(opt.note);
  const [w, h] = PAPERS[opt.paper];
  const line = opt["no-line"]
    ? ""
    : `.note-line { position: fixed; top: 0; bottom: 0; right: 0; border-left: 0.4pt dashed #c8c8c8; }`;
  return `
/* 본문은 원래 용지 그대로 배치하고(오른쪽 여백은 body padding), 그 오른쪽에 필기 띠를 덧붙인다. */
@page { size: ${w + note}mm ${h}mm; margin: ${opt.margin}mm ${note}mm ${opt.margin}mm ${opt.margin}mm; }
body { padding-right: ${opt.margin}mm; }
${line}
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; font-family: -apple-system, "Apple SD Gothic Neo", "Pretendard", sans-serif;
       font-size: 9.5pt; line-height: 1.6; color: #222; word-break: keep-all; overflow-wrap: break-word; }
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
.katex { font-size: 1.08em; }
details { margin: .4em 0; }
summary { font-weight: 600; }
`;
}

function toHtml(mdPath) {
  let body = md.render(readFileSync(mdPath, "utf8"));
  if (!opt["hide-answers"]) body = body.replace(/<details(?![^>]*\bopen\b)/g, "<details open");
  return `<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>${basename(mdPath, ".md")}</title>
<link rel="stylesheet" href="${pathToFileURL(KATEX_CSS)}">
<style>${css()}</style></head>
<body><div class="note-line"></div>${body}</body></html>`;
}

function collect(paths) {
  const files = [];
  for (const p of paths.map((x) => resolve(x))) {
    if (statSync(p).isDirectory()) {
      files.push(...readdirSync(p).filter((f) => f.endsWith(".md") && !f.startsWith("_")).sort().map((f) => join(p, f)));
    } else if (extname(p) === ".md") files.push(p);
  }
  return files;
}

const chrome = process.env.CHROME_PATH ?? CHROME_PATHS.find(existsSync);
if (!chrome) {
  console.error("크롬을 찾지 못했다. CHROME_PATH 환경변수로 실행 파일 경로를 알려 준다.");
  process.exit(1);
}

const files = collect(positionals);
if (files.length === 0) {
  console.error("변환할 .md 파일이 없다.");
  process.exit(1);
}

const work = mkdtempSync(join(tmpdir(), "note-pdf-"));
const browser = await puppeteer.launch({ executablePath: chrome, headless: true });
try {
  const page = await browser.newPage();
  for (const file of files) {
    const htmlPath = join(work, basename(file, ".md") + ".html");
    writeFileSync(htmlPath, toHtml(file));
    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: "networkidle0" });
    await page.evaluate(() => document.fonts.ready);
    const outDir = opt.out ? resolve(opt.out) : join(HERE, "output", basename(dirname(file)));
    mkdirSync(outDir, { recursive: true });
    const outPath = join(outDir, basename(file, ".md") + ".pdf");
    await page.pdf({
      path: outPath,
      preferCSSPageSize: true,
      printBackground: true,
      displayHeaderFooter: true,
      headerTemplate: "<span></span>",
      footerTemplate: `<div style="width:100%;font-size:7pt;color:#999;padding-left:${opt.margin}mm">
        <span class="pageNumber"></span> / <span class="totalPages"></span></div>`,
    });
    console.log(`만듦: ${outPath}`);
  }
} finally {
  await browser.close();
  rmSync(work, { recursive: true, force: true });
}
