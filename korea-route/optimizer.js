/*
 * 방문 순서 최적화 (외부 의존성 없음, 브라우저/Node 공용)
 *
 * - 이동시간 행렬(초)을 받아 출발지에서 시작해 모든 장소를 한 번씩 도는
 *   가장 짧은 순서를 찾는다. (비대칭 행렬 허용: A→B 와 B→A 가 달라도 됨)
 * - 장소가 13곳 이하면 Held-Karp 동적계획법으로 정확한 최적해,
 *   그보다 많으면 최근접 이웃 + 2-opt 개선으로 근사해를 구한다.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.RouteOpt = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const EXACT_LIMIT = 13;

  function haversineKm(a, b) {
    const R = 6371;
    const toRad = (d) => (d * Math.PI) / 180;
    const dLat = toRad(b.lat - a.lat);
    const dLng = toRad(b.lng - a.lng);
    const h =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  /*
   * 직선거리 기반 이동시간 추정(초). 라우팅 서버를 못 쓸 때와
   * 대중교통 모드에서 사용한다. 계수는 한국 도시 기준의 대략적인 값.
   */
  function estimateSeconds(a, b, mode) {
    const km = haversineKm(a, b);
    if (km < 0.05) return 0;
    if (mode === "walk") return ((km * 1.3) / 4.5) * 3600;
    if (mode === "car") {
      const speed = km < 10 ? 25 : km < 40 ? 45 : 70;
      return ((km * 1.3) / speed) * 3600 + 5 * 60;
    }
    // transit: 가까우면 걷고, 멀어질수록 지하철/시외버스/KTX 속도로 가정
    if (km < 1.0) return ((km * 1.3) / 4.5) * 3600;
    const speed = km < 10 ? 18 : km < 40 ? 30 : km < 150 ? 55 : 110;
    const overhead = km < 40 ? 10 : 25; // 대기 + 환승 + 역까지 걷기
    return ((km * 1.3) / speed) * 3600 + overhead * 60;
  }

  function estimateMatrix(points, mode) {
    return points.map((a) => points.map((b) => (a === b ? 0 : estimateSeconds(a, b, mode))));
  }

  function routeCost(matrix, order) {
    let total = 0;
    for (let i = 0; i + 1 < order.length; i++) total += matrix[order[i]][order[i + 1]];
    return total;
  }

  /* Held-Karp: 출발점 고정, 도착점은 자유 또는 고정 */
  function solveExact(matrix, start, end) {
    const n = matrix.length;
    const others = [];
    for (let i = 0; i < n; i++) if (i !== start) others.push(i);
    const m = others.length;
    if (m === 0) return [start];

    const full = (1 << m) - 1;
    const dp = new Float64Array((full + 1) * m).fill(Infinity);
    const parent = new Int32Array((full + 1) * m).fill(-1);

    for (let j = 0; j < m; j++) dp[(1 << j) * m + j] = matrix[start][others[j]];

    for (let mask = 1; mask <= full; mask++) {
      for (let j = 0; j < m; j++) {
        if (!(mask & (1 << j))) continue;
        const cur = dp[mask * m + j];
        if (cur === Infinity) continue;
        // 도착점이 고정이면 마지막이 아닌 한 도착점에서 다른 곳으로 이어지지 않는다
        if (end != null && others[j] === end && mask !== full) continue;
        for (let k = 0; k < m; k++) {
          if (mask & (1 << k)) continue;
          const next = mask | (1 << k);
          const cand = cur + matrix[others[j]][others[k]];
          if (cand < dp[next * m + k]) {
            dp[next * m + k] = cand;
            parent[next * m + k] = j;
          }
        }
      }
    }

    let last = -1;
    if (end != null) {
      last = others.indexOf(end);
    } else {
      let best = Infinity;
      for (let j = 0; j < m; j++) {
        if (dp[full * m + j] < best) {
          best = dp[full * m + j];
          last = j;
        }
      }
    }

    const path = [];
    let mask = full;
    let j = last;
    while (j !== -1) {
      path.push(others[j]);
      const p = parent[mask * m + j];
      mask &= ~(1 << j);
      j = p;
    }
    path.push(start);
    return path.reverse();
  }

  /* 최근접 이웃 + 2-opt (장소가 많을 때) */
  function solveHeuristic(matrix, start, end) {
    const n = matrix.length;
    const visited = new Array(n).fill(false);
    const order = [start];
    visited[start] = true;
    if (end != null) visited[end] = true;
    while (order.length < n - (end != null && end !== start ? 1 : 0)) {
      const cur = order[order.length - 1];
      let best = -1;
      for (let k = 0; k < n; k++) {
        if (!visited[k] && (best === -1 || matrix[cur][k] < matrix[cur][best])) best = k;
      }
      if (best === -1) break;
      visited[best] = true;
      order.push(best);
    }
    if (end != null && end !== start) order.push(end);

    const lo = 1;
    const hi = end != null ? order.length - 2 : order.length - 1;
    let improved = true;
    let bestCost = routeCost(matrix, order);
    while (improved) {
      improved = false;
      for (let i = lo; i < hi; i++) {
        for (let k = i + 1; k <= hi; k++) {
          const cand = order.slice(0, i).concat(order.slice(i, k + 1).reverse(), order.slice(k + 1));
          const c = routeCost(matrix, cand);
          if (c + 1e-9 < bestCost) {
            bestCost = c;
            order.splice(0, order.length, ...cand);
            improved = true;
          }
        }
      }
    }
    return order;
  }

  /*
   * matrix: n×n 이동시간(초)
   * opts.start: 출발 인덱스 (기본 0)
   * opts.end:   도착 인덱스 (없으면 자유)
   * 반환: 방문 순서(인덱스 배열, 출발점 포함)
   */
  function solveOrder(matrix, opts) {
    const o = opts || {};
    const start = o.start == null ? 0 : o.start;
    const end = o.end == null || o.end === start ? null : o.end;
    if (matrix.length <= 1) return matrix.length ? [start] : [];
    if (matrix.length - 1 <= EXACT_LIMIT) return solveExact(matrix, start, end);
    return solveHeuristic(matrix, start, end);
  }

  return { haversineKm, estimateSeconds, estimateMatrix, routeCost, solveOrder, EXACT_LIMIT };
});
