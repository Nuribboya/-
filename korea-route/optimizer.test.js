// 실행: node --test korea-route/
const test = require("node:test");
const assert = require("node:assert");
const R = require("./optimizer.js");

function rng(seed) {
  let s = seed;
  return () => ((s = (s * 1103515245 + 12345) % 2147483648) / 2147483648);
}

function randomMatrix(n, rand) {
  return Array.from({ length: n }, (_, i) =>
    Array.from({ length: n }, (_, j) => (i === j ? 0 : Math.round(rand() * 3600)))
  );
}

function bruteForce(matrix, start, end) {
  const rest = [...matrix.keys()].filter((i) => i !== start && i !== end);
  let best = Infinity;
  const permute = (arr, k) => {
    if (k === arr.length) {
      const order = [start, ...arr, ...(end != null ? [end] : [])];
      best = Math.min(best, R.routeCost(matrix, order));
      return;
    }
    for (let i = k; i < arr.length; i++) {
      [arr[k], arr[i]] = [arr[i], arr[k]];
      permute(arr, k + 1);
      [arr[k], arr[i]] = [arr[i], arr[k]];
    }
  };
  permute(rest, 0);
  return best;
}

function assertValidOrder(order, n, start, end) {
  assert.strictEqual(order.length, n);
  assert.strictEqual(new Set(order).size, n);
  assert.strictEqual(order[0], start);
  if (end != null) assert.strictEqual(order[n - 1], end);
}

test("exact solver matches brute force (free end, asymmetric)", () => {
  const rand = rng(42);
  for (let trial = 0; trial < 30; trial++) {
    const n = 2 + Math.floor(rand() * 7);
    const m = randomMatrix(n, rand);
    const order = R.solveOrder(m, { start: 0 });
    assertValidOrder(order, n, 0);
    assert.strictEqual(R.routeCost(m, order), bruteForce(m, 0, null));
  }
});

test("exact solver matches brute force (fixed end)", () => {
  const rand = rng(7);
  for (let trial = 0; trial < 30; trial++) {
    const n = 3 + Math.floor(rand() * 6);
    const m = randomMatrix(n, rand);
    const end = 1 + Math.floor(rand() * (n - 1));
    const order = R.solveOrder(m, { start: 0, end });
    assertValidOrder(order, n, 0, end);
    assert.strictEqual(R.routeCost(m, order), bruteForce(m, 0, end));
  }
});

test("non-zero start index", () => {
  const m = randomMatrix(6, rng(3));
  const order = R.solveOrder(m, { start: 4 });
  assertValidOrder(order, 6, 4);
  assert.strictEqual(R.routeCost(m, order), bruteForce(m, 4, null));
});

test("heuristic handles many places and fixed end", () => {
  const rand = rng(99);
  const pts = Array.from({ length: 25 }, () => ({ lat: 35 + rand(), lng: 129 + rand() }));
  const m = R.estimateMatrix(pts, "car");
  const free = R.solveOrder(m, { start: 0 });
  assertValidOrder(free, 25, 0);
  const fixed = R.solveOrder(m, { start: 0, end: 10 });
  assertValidOrder(fixed, 25, 0, 10);
  const identity = [...Array(25).keys()];
  assert.ok(R.routeCost(m, free) < R.routeCost(m, identity));
});

test("trivial sizes", () => {
  assert.deepStrictEqual(R.solveOrder([], {}), []);
  assert.deepStrictEqual(R.solveOrder([[0]], {}), [0]);
  assert.deepStrictEqual(R.solveOrder([[0, 5], [5, 0]], {}), [0, 1]);
});

test("Busan example: west-side spots grouped before east-side spots", () => {
  const busanStation = { lat: 35.1151, lng: 129.0422 };
  const haeundae = { lat: 35.1587, lng: 129.1604 };
  const gamcheon = { lat: 35.0975, lng: 129.0106 };
  const gwangalli = { lat: 35.1532, lng: 129.1186 };
  const jagalchi = { lat: 35.0966, lng: 129.0306 };
  const pts = [busanStation, haeundae, gamcheon, gwangalli, jagalchi];
  const m = R.estimateMatrix(pts, "transit");
  const order = R.solveOrder(m, { start: 0 });
  // 부산역 → (자갈치/감천: 서쪽) → (광안리 → 해운대: 동쪽)
  assert.deepStrictEqual(new Set(order.slice(1, 3)), new Set([2, 4]));
  assert.deepStrictEqual(order.slice(3), [3, 1]);
  assert.ok(R.routeCost(m, order) < R.routeCost(m, [0, 1, 2, 3, 4]));
});

test("transit estimate grows with distance and stays sane", () => {
  const a = { lat: 35.1151, lng: 129.0422 }; // 부산역
  const near = { lat: 35.1166, lng: 129.0422 };
  const gimhae = { lat: 35.2285, lng: 128.8894 };
  const seoul = { lat: 37.5547, lng: 126.9707 };
  const t1 = R.estimateSeconds(a, near, "transit");
  const t2 = R.estimateSeconds(a, gimhae, "transit");
  const t3 = R.estimateSeconds(a, seoul, "transit");
  assert.ok(t1 < t2 && t2 < t3);
  assert.ok(t3 / 3600 > 2 && t3 / 3600 < 5, `Busan→Seoul ${t3 / 3600}h`);
});
