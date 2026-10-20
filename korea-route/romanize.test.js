// 실행: node --test korea-route/romanize.test.js
const test = require("node:test");
const assert = require("node:assert");
const { romanize, hasHangul } = require("./romanize.js");

test("official spellings of common place names", () => {
  const cases = {
    해운대: "Haeundae", 광안리: "Gwangalli", 경복궁: "Gyeongbokgung", 신라: "Silla", 종로: "Jongno",
    독립문: "Dongnimmun", 한라산: "Hallasan", 서면: "Seomyeon", 김해: "Gimhae", 속초: "Sokcho",
    국밥: "Gukbap", 닭갈비: "Dakgalbi", 을지로: "Euljiro", 남포동: "Nampodong", 해동용궁사: "Haedongyonggungsa",
  };
  for (const [ko, en] of Object.entries(cases)) assert.strictEqual(romanize(ko), en, ko);
});

test("keeps non-Hangul text and spacing", () => {
  assert.strictEqual(romanize("CU 편의점"), "CU Pyeonuijeom");
  assert.strictEqual(romanize("을지로3가"), "Euljiro3ga");
  assert.strictEqual(romanize("Busan Station"), "Busan Station");
  assert.ok(hasHangul("부산 Station") && !hasHangul("Busan"));
});
