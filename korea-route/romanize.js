/*
 * 한글 → 로마자 (국어의 로마자 표기법 기반, 간이 버전)
 * 영어 이름이 없는 장소(작은 가게 등)를 외국인에게 보여줄 때 쓴다.
 * 연음, ㄴ+ㄹ/ㄹ+ㄹ → ll, 비음화(ㄱㄷㅂ + ㄴㅁㄹ, ㅁㅇ + ㄹ) 정도만 반영한다.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Romanize = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const INITIAL = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"];
  const MEDIAL = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"];
  // 받침이 단어 끝/자음 앞에 올 때의 대표음
  const FINAL = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t"];
  // 다음 음절이 ㅇ으로 시작할 때 넘어가는 소리 [남는 받침, 넘어가는 초성]
  const LIAISON = {
    1: ["", "g"], 2: ["", "kk"], 3: ["k", "s"], 4: ["", "n"], 5: ["n", "j"], 6: ["", "n"], 7: ["", "d"],
    8: ["", "r"], 9: ["l", "g"], 10: ["l", "m"], 11: ["l", "b"], 12: ["l", "s"], 13: ["l", "t"], 14: ["l", "p"],
    15: ["", "r"], 16: ["", "m"], 17: ["", "b"], 18: ["p", "s"], 19: ["", "s"], 20: ["", "ss"], 21: ["ng", ""],
    22: ["", "j"], 23: ["", "ch"], 24: ["", "k"], 25: ["", "t"], 26: ["", "p"], 27: ["", ""],
  };
  const NIEUN = 2, RIEUL = 5, MIEUM = 6, IEUNG = 11;

  function decompose(ch) {
    const code = ch.charCodeAt(0) - 0xac00;
    if (code < 0 || code > 11171) return null;
    return { i: Math.floor(code / 588), m: Math.floor((code % 588) / 28), f: code % 28 };
  }

  function romanizeWord(word) {
    const sylls = Array.from(word).map((ch) => decompose(ch) || ch);
    let out = "";
    for (let k = 0; k < sylls.length; k++) {
      const s = sylls[k];
      if (typeof s === "string") { out += s; continue; }
      const prev = sylls[k - 1];
      const next = sylls[k + 1];
      let initial = INITIAL[s.i];
      // 앞 음절에서 연음으로 넘어온 초성
      if (prev && typeof prev === "object" && s.i === IEUNG && LIAISON[prev.f]) initial = LIAISON[prev.f][1];
      if (k === 0 && s.i === RIEUL) initial = "r";
      if (prev && typeof prev === "object" && s.i === RIEUL && prev.f) {
        initial = prev.f === 8 || prev.f === 4 ? "l" : "n"; // 신라 Silla, 종로 Jongno, 독립 Dongnip
      }
      out += initial + MEDIAL[s.m];

      if (!s.f) continue;
      let coda = FINAL[s.f];
      if (next && typeof next === "object") {
        if (next.i === IEUNG && LIAISON[s.f]) coda = LIAISON[s.f][0];
        else if (next.i === RIEUL && (s.f === 4 || s.f === 8)) coda = "l";
        else if (next.i === NIEUN || next.i === MIEUM || next.i === RIEUL) {
          if (coda === "k") coda = "ng";
          else if (coda === "t") coda = "n";
          else if (coda === "p") coda = "m";
        }
      }
      out += coda;
    }
    return out;
  }

  function hasHangul(s) {
    return /[가-힣]/.test(s);
  }

  function romanize(text) {
    return String(text)
      .split(/(\s+)/)
      .map((w) => (hasHangul(w) ? capitalize(romanizeWord(w)) : w))
      .join("");
  }

  function capitalize(w) {
    return w.charAt(0).toUpperCase() + w.slice(1);
  }

  return { romanize, hasHangul };
});
