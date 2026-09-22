// Turns the published artifact page into a standalone offline document for the
// desktop build: no CDN script, no webfont link, wrapped in a real HTML skeleton.
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'index.html');
const OUT_DIR = path.join(__dirname, 'app');
const XLSX = path.join(__dirname, 'node_modules', 'xlsx', 'dist', 'xlsx.full.min.js');

let html = fs.readFileSync(SRC, 'utf8');

const fontLinks = /<link rel="(?:preconnect|stylesheet)"[^>]*fonts\.(?:googleapis|gstatic)\.com[^>]*>\n?/g;
const fontCount = (html.match(fontLinks) || []).length;
html = html.replace(fontLinks, '');

// SheetJS ships as its own file rather than inlined: its source contains the
// literal "</script>", which would close the tag early and spill the rest as text.
const cdnTag = /<script src="https:\/\/cdnjs\.cloudflare\.com[^"]*"><\/script>/;
if (!cdnTag.test(html)) {
  throw new Error('index.html 에서 SheetJS CDN 태그를 찾지 못했습니다. build-html.js 의 패턴을 맞춰 주세요.');
}
html = html.replace(cdnTag, '<script src="vendor/xlsx.full.min.js"></script>');

const split = html.indexOf('</style>');
if (split < 0) throw new Error('index.html 에서 </style> 를 찾지 못했습니다.');
const head = html.slice(0, split + '</style>'.length);
const body = html.slice(split + '</style>'.length);

const doc = `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:">
<style>[hidden]{display:none!important}img{max-width:100%}</style>
${head}
</head>
<body>
${body}
</body>
</html>
`;

fs.mkdirSync(path.join(OUT_DIR, 'vendor'), { recursive: true });
fs.copyFileSync(XLSX, path.join(OUT_DIR, 'vendor', 'xlsx.full.min.js'));
fs.writeFileSync(path.join(OUT_DIR, 'index.html'), doc);
console.log('app/index.html 생성 — ' + (doc.length / 1024).toFixed(0) + 'KB (웹폰트 링크 ' + fontCount + '개 제거)');
console.log('app/vendor/xlsx.full.min.js 복사 — ' + (fs.statSync(XLSX).size / 1024).toFixed(0) + 'KB');
