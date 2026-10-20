/* Korea Route Planner – UI. 계산은 optimizer.js(RouteOpt)가 담당. */
(function () {
  "use strict";

  const CONFIG = window.APP_CONFIG || {};
  const STORAGE_KEY = "korea-route-v1";
  const END_SUFFIX = "__end"; // 왕복일 때 출발지를 복제한 도착점 id
  const KOREA_BBOX = "124.5,33.0,131.0,38.7";
  const ROUTERS = {
    car: "https://router.project-osrm.org",
    walk: "https://routing.openstreetmap.de/routed-foot",
  };
  // [한국어 이름, 영어 이름, 위도, 경도]
  const PRESETS = {
    busan: [
      ["부산역", "Busan Station", 35.1151, 129.0422],
      ["해운대해수욕장", "Haeundae Beach", 35.1587, 129.1604],
      ["감천문화마을", "Gamcheon Culture Village", 35.0975, 129.0106],
      ["광안리해수욕장", "Gwangalli Beach", 35.1532, 129.1186],
      ["자갈치시장", "Jagalchi Market", 35.0966, 129.0306],
      ["해동용궁사", "Haedong Yonggungsa Temple", 35.1884, 129.2233],
      ["서면", "Seomyeon", 35.1578, 129.06],
    ],
    southeast: [
      ["부산역", "Busan Station", 35.1151, 129.0422],
      ["경주 불국사", "Gyeongju (Bulguksa)", 35.7901, 129.3321],
      ["김해 수로왕릉", "Gimhae (Suro King's Tomb)", 35.2333, 128.8775],
      ["통영", "Tongyeong", 34.8544, 128.4331],
      ["울산", "Ulsan", 35.5384, 129.3114],
    ],
  };
  const TILES = {
    // 영어: 라틴 문자 라벨 지도 / 한국어: OSM 기본(현지어 라벨)
    en: ["https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", "© OpenStreetMap © CARTO"],
    ko: ["https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", "© OpenStreetMap"],
  };

  const I18N = {
    en: {
      title: "Korea Route Planner",
      tagline: "Add the places you want to visit. We'll find the fastest order and re-plan from wherever you are when things change.",
      searchPh: "Search a place (e.g. Haeundae, Gyeongbokgung)",
      mapHint: "Tip: you can also tap the map to drop a pin.",
      tryExample: "Try:",
      presetBusan: "Busan day trip",
      presetSoutheast: "Busan → Gyeongju → Tongyeong",
      empty: "No places yet. Search above or tap the map.",
      mode: "Getting around",
      modeTransit: "Public transit",
      modeWalk: "Walking",
      modeCar: "Car / taxi",
      roundTrip: "Return to start (round trip)",
      endAtLast: "Finish at the last place in my list (e.g. hotel, station)",
      optimize: "Find best order",
      replan: "📍 Re-plan from here",
      start: "Start",
      end: "End",
      setStart: "Make this the start",
      markDone: "Mark as visited",
      remove: "Remove",
      needTwo: "Add at least 2 places.",
      computing: "Calculating…",
      locating: "Getting your location…",
      noGps: "Couldn't get your location. Check location permission.",
      allDone: "You've visited everything. Nice trip!",
      total: "Total travel time ≈ {t}",
      saving: "{t} faster than the order you entered",
      already: "Your order was already the fastest 👍",
      youAreHere: "You are here",
      estimated: "estimated",
      routerFail: "Routing server unavailable – using estimated times.",
      estimateNote: "Transit times are estimates based on distance. Tap Kakao or Google on each leg for live routes.",
      share: "Copy share link",
      copied: "Link copied!",
      clear: "Clear all",
      confirmClear: "Remove all places?",
      pin: "Pinned spot",
      searchFail: "Search failed. Check your connection.",
      noResults: "No results",
    },
    ko: {
      title: "여행 동선 플래너",
      tagline: "가고 싶은 곳을 넣으면 가장 빠른 방문 순서를 찾아줘요. 계획이 틀어지면 지금 위치에서 바로 다시 짜드려요.",
      searchPh: "장소 검색 (예: 해운대, 경복궁)",
      mapHint: "지도를 눌러서 핀으로 추가할 수도 있어요.",
      tryExample: "예시:",
      presetBusan: "부산 당일치기",
      presetSoutheast: "부산 → 경주 → 통영",
      empty: "아직 장소가 없어요. 위에서 검색하거나 지도를 눌러보세요.",
      mode: "이동 수단",
      modeTransit: "대중교통",
      modeWalk: "도보",
      modeCar: "자동차 / 택시",
      roundTrip: "출발지로 돌아오기 (왕복)",
      endAtLast: "목록 맨 마지막 장소에서 끝내기 (숙소, 역 등)",
      optimize: "최적 순서 찾기",
      replan: "📍 지금 위치에서 다시 짜기",
      start: "출발",
      end: "도착",
      setStart: "출발지로 지정",
      markDone: "방문 완료",
      remove: "삭제",
      needTwo: "장소를 2곳 이상 넣어주세요.",
      computing: "계산 중…",
      locating: "현재 위치 확인 중…",
      noGps: "현재 위치를 가져오지 못했어요. 위치 권한을 확인해주세요.",
      allDone: "모든 곳을 다 들렀어요. 즐거운 여행 되세요!",
      total: "총 이동시간 약 {t}",
      saving: "입력한 순서보다 {t} 빨라요",
      already: "입력한 순서가 이미 최적이에요 👍",
      youAreHere: "현재 위치",
      estimated: "추정",
      routerFail: "경로 서버에 연결하지 못해 추정 시간으로 계산했어요.",
      estimateNote: "대중교통 시간은 거리 기반 추정치예요. 구간별 Kakao / Google 링크를 누르면 실제 경로를 볼 수 있어요.",
      share: "공유 링크 복사",
      copied: "링크를 복사했어요!",
      clear: "전체 삭제",
      confirmClear: "모든 장소를 삭제할까요?",
      pin: "지정한 위치",
      searchFail: "검색에 실패했어요. 인터넷 연결을 확인해주세요.",
      noResults: "검색 결과가 없어요",
    },
  };

  const $ = (sel) => document.querySelector(sel);
  const els = {
    search: $("#search"),
    suggestions: $("#suggestions"),
    places: $("#places"),
    empty: $("#empty"),
    mode: $("#mode"),
    roundTrip: $("#roundTrip"),
    endAtLast: $("#endAtLast"),
    optimize: $("#optimize"),
    replan: $("#replan"),
    status: $("#status"),
    result: $("#result"),
    summary: $("#summary"),
    steps: $("#steps"),
  };

  let state = loadState();
  let map = null;
  let layer = null;
  let busy = false;

  /* ---------- 상태 저장/복원 ---------- */

  function defaultState() {
    const ko = (navigator.language || "").toLowerCase().startsWith("ko");
    return { lang: ko ? "ko" : "en", mode: "transit", roundTrip: false, endAtLast: false, places: [], result: null };
  }

  function loadState() {
    const s = defaultState();
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      if (saved && Array.isArray(saved.places)) Object.assign(s, saved);
    } catch (e) { /* 저장소를 못 쓰는 환경이면 기본값으로 */ }
    const shared = readShareHash();
    if (shared) {
      s.places = shared.places;
      s.mode = shared.mode || s.mode;
      s.result = null;
    }
    return s;
  }

  function saveState() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (e) { /* 무시 */ }
  }

  function readShareHash() {
    const m = location.hash.match(/^#p=(.+)$/);
    if (!m) return null;
    try {
      const bin = atob(m[1].replace(/-/g, "+").replace(/_/g, "/"));
      const data = JSON.parse(new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0))));
      history.replaceState(null, "", location.pathname + location.search);
      return {
        mode: data.m,
        places: data.p.map(([name, lat, lng, en]) => newPlace(String(name), +lat, +lng, en ? String(en) : "")),
      };
    } catch (e) {
      return null;
    }
  }

  function shareUrl() {
    const data = {
      m: state.mode,
      p: state.places.map((p) => [p.name, +p.lat.toFixed(5), +p.lng.toFixed(5)].concat(p.en ? [p.en] : [])),
    };
    const bytes = new TextEncoder().encode(JSON.stringify(data));
    const b64 = btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    return location.origin + location.pathname + "#p=" + b64;
  }

  /* ---------- 유틸 ---------- */

  function t(key, vars) {
    let s = (I18N[state.lang] || I18N.en)[key] || I18N.en[key] || key;
    if (vars) for (const k in vars) s = s.replace("{" + k + "}", vars[k]);
    return s;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function fmtDuration(sec) {
    const mins = Math.max(1, Math.round(sec / 60));
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    if (state.lang === "ko") return ((h ? h + "시간 " : "") + (m || !h ? m + "분" : "")).trim();
    return ((h ? h + " h " : "") + (m || !h ? m + " min" : "")).trim();
  }

  function fmtKm(km) {
    return km < 1 ? Math.round(km * 1000) + " m" : km.toFixed(1) + " km";
  }

  /* name: 현지(한국어) 이름, en: 영어 이름(없으면 빈 문자열) */
  function newPlace(name, lat, lng, en) {
    return { id: Math.random().toString(36).slice(2, 10), name, en: en || "", lat, lng, done: false };
  }

  function displayName(p) {
    if (state.lang === "ko") return p.name || p.en;
    if (p.en) return p.en;
    return Romanize.hasHangul(p.name) ? Romanize.romanize(p.name) : p.name;
  }

  /* 영어 화면에서 한글 이름을 같이 보여준다 (택시 기사/가게에 보여주기용) */
  function localName(p) {
    return state.lang === "en" && Romanize.hasHangul(p.name) ? p.name : "";
  }

  /* OSM 이름 태그에서 한국어/영어 이름을 고른다 */
  function pickNames(tags, fallback) {
    const t = tags || {};
    const name = t["name:ko"] || t.name || fallback || "";
    let en = t["name:en"] || "";
    if (en === name || Romanize.hasHangul(en)) en = "";
    return { name, en };
  }

  function setStatus(msg) { els.status.textContent = msg || ""; }

  async function fetchJson(url, ms) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), ms || 8000);
    try {
      const res = await fetch(url, { signal: ctrl.signal });
      if (!res.ok) throw new Error("HTTP " + res.status);
      return await res.json();
    } finally {
      clearTimeout(timer);
    }
  }

  /* ---------- 이동시간 행렬 / 경로 ---------- */

  async function getMatrix(points, mode) {
    const base = ROUTERS[mode];
    if (base && points.length <= 60) {
      try {
        const coords = points.map((p) => p.lng + "," + p.lat).join(";");
        const data = await fetchJson(base + "/table/v1/driving/" + coords + "?annotations=duration,distance");
        if (data.code === "Ok") {
          const fallback = RouteOpt.estimateMatrix(points, mode);
          const durations = data.durations.map((row, i) => row.map((v, j) => (v == null ? fallback[i][j] : v)));
          return { durations, distances: data.distances, estimated: false };
        }
      } catch (e) { /* 아래 추정치로 */ }
      setStatus(t("routerFail"));
    }
    return { durations: RouteOpt.estimateMatrix(points, mode), distances: null, estimated: true };
  }

  async function getGeometry(points, mode) {
    const base = ROUTERS[mode];
    if (base && points.length >= 2) {
      try {
        const coords = points.map((p) => p.lng + "," + p.lat).join(";");
        const data = await fetchJson(base + "/route/v1/driving/" + coords + "?overview=full&geometries=geojson");
        if (data.code === "Ok") return data.routes[0].geometry.coordinates.map(([lng, lat]) => [lat, lng]);
      } catch (e) { /* 직선으로 */ }
    }
    return null;
  }

  /*
   * 문제 구성: points[0] 이 출발점.
   * - 일반: 목록의 첫 장소에서 출발, 모든 장소 포함
   * - 다시 짜기: 현재 위치에서 출발, 방문 완료한 곳은 제외
   */
  function buildProblem(here) {
    const all = state.places;
    const startPlace = all[0];
    const origin = here ? { id: "__here", name: t("youAreHere"), lat: here.lat, lng: here.lng, here: true } : startPlace;
    let pool = here ? all.filter((p) => !p.done) : all.slice(1);

    let end = null;
    if (state.roundTrip) {
      pool = pool.filter((p) => p !== startPlace);
      end = { ...startPlace, id: startPlace.id + END_SUFFIX };
    } else if (state.endAtLast && pool.includes(all[all.length - 1])) {
      end = all[all.length - 1];
      pool = pool.filter((p) => p !== end);
    }

    const points = [origin, ...pool];
    if (end) points.push(end);
    return { points, endIndex: end ? points.length - 1 : null };
  }

  async function plan(here) {
    if (busy) return;
    if (!here && state.places.length < 2) return setStatus(t("needTwo"));
    if (here && !state.places.some((p) => !p.done)) return setStatus(t("allDone"));
    const { points, endIndex } = buildProblem(here);

    busy = true;
    els.optimize.disabled = els.replan.disabled = true;
    setStatus(t("computing"));
    try {
      const { durations, distances, estimated } = await getMatrix(points, state.mode);
      const order = RouteOpt.solveOrder(durations, { start: 0, end: endIndex });
      const naive = [...points.keys()];
      const ordered = order.map((i) => points[i]);
      const legs = [];
      for (let k = 0; k + 1 < order.length; k++) {
        const a = order[k];
        const b = order[k + 1];
        legs.push({
          sec: durations[a][b],
          km: distances ? distances[a][b] / 1000 : RouteOpt.haversineKm(points[a], points[b]),
        });
      }
      const geometry = await getGeometry(ordered, state.mode);
      state.result = {
        stops: ordered.map((p) => ({ id: p.id, name: p.name, en: p.en || "", lat: p.lat, lng: p.lng, here: !!p.here })),
        legs,
        total: RouteOpt.routeCost(durations, order),
        naiveTotal: RouteOpt.routeCost(durations, naive),
        estimated: estimated || state.mode === "transit",
        mode: state.mode,
        geometry,
      };
      if (!estimated || state.mode === "transit") setStatus("");
      saveState();
      render();
      fitMap();
      els.result.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } finally {
      busy = false;
      els.optimize.disabled = els.replan.disabled = false;
    }
  }

  function replanFromHere() {
    if (!navigator.geolocation) return setStatus(t("noGps"));
    setStatus(t("locating"));
    navigator.geolocation.getCurrentPosition(
      (pos) => plan({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      () => setStatus(t("noGps")),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
    );
  }

  /* ---------- 장소 편집 ---------- */

  function addPlace(name, lat, lng, en) {
    state.places.push(newPlace(name, lat, lng, en));
    state.result = null;
    saveState();
    render();
    fitMap();
  }

  function onPlaceAction(e) {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const id = btn.closest("li").dataset.id;
    const idx = state.places.findIndex((p) => p.id === id);
    if (idx < 0) return;
    const act = btn.dataset.act;
    if (act === "remove") {
      state.places.splice(idx, 1);
      state.result = null;
    } else if (act === "start") {
      state.places.unshift(state.places.splice(idx, 1)[0]);
      state.result = null;
    } else if (act === "done") {
      state.places[idx].done = !state.places[idx].done;
    }
    saveState();
    render();
  }

  /* ---------- 검색 ---------- */

  let searchTimer = null;
  let searchSeq = 0;
  let suggestions = [];
  let activeIdx = -1;

  /* 카카오 장소 검색: 한국 가게/식당 데이터가 OSM보다 훨씬 풍부하다 (JS 키가 있을 때만) */
  let kakaoPlaces = null;

  function loadKakao() {
    if (!CONFIG.kakaoJsKey) return Promise.resolve(null);
    if (!kakaoPlaces) {
      kakaoPlaces = new Promise((resolve) => {
        const s = document.createElement("script");
        s.src = "https://dapi.kakao.com/v2/maps/sdk.js?autoload=false&libraries=services&appkey=" + encodeURIComponent(CONFIG.kakaoJsKey);
        s.onload = () => kakao.maps.load(() => resolve(new kakao.maps.services.Places()));
        s.onerror = () => resolve(null);
        document.head.appendChild(s);
      });
    }
    return kakaoPlaces;
  }

  async function searchKakao(q) {
    const places = await loadKakao();
    if (!places) return null;
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve(null), 6000);
      places.keywordSearch(q, (data, status) => {
        clearTimeout(timer);
        const S = kakao.maps.services.Status;
        if (status === S.ZERO_RESULT) return resolve([]);
        if (status !== S.OK) return resolve(null);
        resolve(data.map((d) => ({
          name: d.place_name,
          en: "",
          sub: (d.road_address_name || d.address_name).split(" ").slice(0, 2).join(" "),
          lat: +d.y,
          lng: +d.x,
        })));
      }, { size: 6 });
    });
  }

  async function searchOsm(q) {
    try {
      // Photon은 한 번에 한 언어만 돌려줘서, 현지어/영어를 각각 받아 OSM id로 합친다
      const base = "https://photon.komoot.io/api/?q=" + encodeURIComponent(q) + "&limit=6&bbox=" + KOREA_BBOX;
      const [local, english] = await Promise.all([
        fetchJson(base, 6000),
        fetchJson(base + "&lang=en", 6000).catch(() => ({ features: [] })),
      ]);
      const key = (p) => p.osm_type + p.osm_id;
      const enById = new Map(english.features.map((f) => [key(f.properties), f.properties]));
      return local.features.map((f) => {
        const p = f.properties;
        const pe = enById.get(key(p)) || {};
        const sub = [pe.district || p.district, pe.city || p.city || pe.county || p.county, pe.state || p.state]
          .filter(Boolean).join(", ");
        const names = pickNames({ name: p.name, "name:en": pe.name }, sub);
        return { ...names, sub, lat: f.geometry.coordinates[1], lng: f.geometry.coordinates[0] };
      }).filter((x) => x.name);
    } catch (e) {
      try {
        const data = await fetchJson(
          "https://nominatim.openstreetmap.org/search?format=jsonv2&namedetails=1&countrycodes=kr&limit=6&accept-language=" +
            state.lang + "&q=" + encodeURIComponent(q), 6000
        );
        return data.map((d) => ({
          ...pickNames(d.namedetails, d.display_name.split(",")[0]),
          sub: d.display_name.split(",").slice(1, 3).join(","),
          lat: +d.lat,
          lng: +d.lon,
        }));
      } catch (e2) {
        return null;
      }
    }
  }

  /*
   * 카카오와 OSM 결과를 합친다. 같은 장소(150m 이내)는 카카오 쪽을 남기고
   * OSM 의 영어 이름만 가져온다. 한글로 검색하면 카카오 결과를 먼저 보여준다.
   */
  function mergeResults(q, kakaoItems, osmItems) {
    const k = kakaoItems || [];
    const rest = (osmItems || []).filter((o) => {
      const same = k.find((x) => RouteOpt.haversineKm(x, o) < 0.15);
      if (!same) return true;
      if (!same.en && o.en) same.en = o.en;
      return false;
    });
    const kakaoFirst = state.lang === "ko" || Romanize.hasHangul(q);
    return (kakaoFirst ? k.concat(rest) : rest.concat(k)).slice(0, 8);
  }

  async function search(q) {
    const seq = ++searchSeq;
    const [kakaoItems, osmItems] = await Promise.all([searchKakao(q), searchOsm(q)]);
    if (seq !== searchSeq) return;
    if (kakaoItems === null && osmItems === null) return showSuggestions(null);
    showSuggestions(mergeResults(q, kakaoItems, osmItems));
  }

  function showSuggestions(items) {
    suggestions = items || [];
    activeIdx = -1;
    if (items === null) {
      els.suggestions.innerHTML = "<li>" + esc(t("searchFail")) + "</li>";
    } else if (!items.length) {
      els.suggestions.innerHTML = "<li>" + esc(t("noResults")) + "</li>";
    } else {
      els.suggestions.innerHTML = items
        .map((s, i) => {
          const area = state.lang === "en" && Romanize.hasHangul(s.sub) ? Romanize.romanize(s.sub) : s.sub;
          const sub = [localName(s), area].filter(Boolean).join(" · ");
          return '<li data-i="' + i + '">' + esc(displayName(s)) + (sub ? "<small>" + esc(sub) + "</small>" : "") + "</li>";
        })
        .join("");
    }
    els.suggestions.hidden = false;
  }

  function hideSuggestions() {
    els.suggestions.hidden = true;
    suggestions = [];
  }

  function pickSuggestion(i) {
    const s = suggestions[i];
    if (!s) return;
    addPlace(s.name, s.lat, s.lng, s.en);
    els.search.value = "";
    hideSuggestions();
  }

  /* ---------- 렌더링 ---------- */

  function applyI18n() {
    document.documentElement.lang = state.lang;
    document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    document.querySelectorAll("[data-i18n-ph]").forEach((el) => { el.placeholder = t(el.dataset.i18nPh); });
    document.querySelectorAll("[data-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === state.lang)));
  }

  function render() {
    applyI18n();
    els.mode.value = state.mode;
    els.roundTrip.checked = state.roundTrip;
    els.endAtLast.checked = state.endAtLast;
    els.endAtLast.disabled = state.roundTrip;

    const n = state.places.length;
    els.empty.hidden = n > 0;
    els.places.innerHTML = state.places
      .map((p, i) => {
        let badge = "";
        if (i === 0) badge = '<span class="badge">' + esc(t("start")) + "</span>";
        else if (i === n - 1 && state.endAtLast && !state.roundTrip) badge = '<span class="badge">' + esc(t("end")) + "</span>";
        return (
          '<li data-id="' + p.id + '"' + (p.done ? ' class="done"' : "") + ">" +
          '<span class="name">' + esc(displayName(p)) + (localName(p) ? "<small>" + esc(localName(p)) + "</small>" : "") + "</span>" + badge +
          (i > 0 ? '<button type="button" class="icon" data-act="start" title="' + esc(t("setStart")) + '" aria-label="' + esc(t("setStart")) + '">★</button>' : "") +
          '<button type="button" class="icon" data-act="done" aria-pressed="' + p.done + '" title="' + esc(t("markDone")) + '" aria-label="' + esc(t("markDone")) + '">✓</button>' +
          '<button type="button" class="icon" data-act="remove" title="' + esc(t("remove")) + '" aria-label="' + esc(t("remove")) + '">✕</button>' +
          "</li>"
        );
      })
      .join("");

    renderResult();
    renderMap();
  }

  function directionsLinks(a, b, mode) {
    const kakao =
      "https://map.kakao.com/link/from/" + encodeURIComponent(a.name.replace(/,/g, " ")) + "," + a.lat + "," + a.lng +
      "/to/" + encodeURIComponent(b.name.replace(/,/g, " ")) + "," + b.lat + "," + b.lng;
    const gmode = mode === "walk" ? "walking" : mode === "car" ? "driving" : "transit";
    const google =
      "https://www.google.com/maps/dir/?api=1&origin=" + a.lat + "," + a.lng + "&destination=" + b.lat + "," + b.lng + "&travelmode=" + gmode;
    return (
      '<a href="' + kakao + '" target="_blank" rel="noopener">Kakao</a>' +
      '<a href="' + google + '" target="_blank" rel="noopener">Google</a>'
    );
  }

  function renderResult() {
    const r = state.result;
    els.result.hidden = !r;
    if (!r) return;

    const saved = r.naiveTotal - r.total;
    els.summary.innerHTML =
      esc(t("total", { t: fmtDuration(r.total) })) +
      (r.estimated ? " (" + esc(t("estimated")) + ")" : "") +
      '<span class="saving">' +
      esc(saved >= 60 ? t("saving", { t: fmtDuration(saved) }) : t("already")) +
      "</span>";

    const doneIds = new Set(state.places.filter((p) => p.done).map((p) => p.id));
    let num = 0;
    els.steps.innerHTML = r.stops
      .map((s, i) => {
        const label = s.here ? "●" : s.id.endsWith(END_SUFFIX) ? "↩" : String(++num);
        const name = s.here ? t("youAreHere") : displayName(s);
        const local = s.here ? "" : localName(s);
        const done = doneIds.has(s.id) ? ' style="text-decoration:line-through;opacity:.6"' : "";
        let html = '<li><div class="step-place"><span class="num' + (s.here ? " here" : "") + '">' + label + "</span>" +
          "<span" + done + ">" + esc(name) + (local ? "<small>" + esc(local) + "</small>" : "") + "</span></div>";
        if (i < r.legs.length) {
          const leg = r.legs[i];
          html += '<div class="leg">' + fmtDuration(leg.sec) + " · " + fmtKm(leg.km) + directionsLinks(s, r.stops[i + 1], r.mode) + "</div>";
        }
        return html + "</li>";
      })
      .join("");
  }

  /* ---------- 지도 ---------- */

  function initMap() {
    if (typeof L === "undefined") return;
    map = L.map("map", { zoomControl: true }).setView([36.3, 127.8], 7);
    layer = L.layerGroup().addTo(map);
    map.on("click", onMapClick);
  }

  async function onMapClick(e) {
    const { lat, lng } = e.latlng;
    let names = { name: t("pin") + " " + (state.places.length + 1), en: "" };
    try {
      const d = await fetchJson(
        "https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=18&namedetails=1&accept-language=ko&lat=" + lat + "&lon=" + lng, 5000
      );
      if (d && (d.namedetails || d.display_name)) {
        names = pickNames(d.namedetails, d.display_name.split(",").slice(0, 2).join(","));
      }
    } catch (err) { /* 기본 이름 사용 */ }
    addPlace(names.name, lat, lng, names.en);
  }

  function pinIcon(label, cls) {
    return L.divIcon({ className: "", html: '<div class="pin ' + (cls || "") + '">' + esc(label) + "</div>", iconSize: [26, 26], iconAnchor: [13, 13] });
  }

  let tiles = null;
  let tilesLang = null;

  function renderMap() {
    if (!map) return;
    if (tilesLang !== state.lang) {
      if (tiles) map.removeLayer(tiles);
      const [url, attribution] = TILES[state.lang] || TILES.en;
      tiles = L.tileLayer(url, { maxZoom: 19, subdomains: "abc", attribution }).addTo(map);
      tilesLang = state.lang;
    }
    layer.clearLayers();
    const r = state.result;
    if (r) {
      const line = r.geometry || r.stops.map((s) => [s.lat, s.lng]);
      L.polyline(line, { color: "#2563eb", weight: 4, opacity: 0.8, dashArray: r.geometry ? null : "6 8" }).addTo(layer);
      let num = 0;
      r.stops.forEach((s) => {
        if (s.id.endsWith(END_SUFFIX)) return; // 왕복 도착점은 출발점과 같은 위치
        const label = s.here ? "●" : String(++num);
        L.marker([s.lat, s.lng], { icon: pinIcon(label, s.here ? "here" : "") }).bindTooltip(s.here ? t("youAreHere") : displayName(s)).addTo(layer);
      });
    } else {
      state.places.forEach((p) => {
        L.marker([p.lat, p.lng], { icon: pinIcon("•", "pending") }).bindTooltip(displayName(p)).addTo(layer);
      });
    }
  }

  function fitMap() {
    if (!map) return;
    const pts = state.result ? state.result.stops : state.places;
    if (!pts.length) return;
    if (pts.length === 1) map.setView([pts[0].lat, pts[0].lng], 14);
    else map.fitBounds(pts.map((p) => [p.lat, p.lng]), { padding: [30, 30], maxZoom: 15 });
  }

  /* ---------- 이벤트 ---------- */

  function bind() {
    document.querySelectorAll("[data-lang]").forEach((b) =>
      b.addEventListener("click", () => { state.lang = b.dataset.lang; saveState(); render(); })
    );

    els.search.addEventListener("input", () => {
      clearTimeout(searchTimer);
      const q = els.search.value.trim();
      if (q.length < 2) return hideSuggestions();
      searchTimer = setTimeout(() => search(q), 350);
    });
    els.search.addEventListener("keydown", (e) => {
      if (els.suggestions.hidden || !suggestions.length) return;
      const items = els.suggestions.querySelectorAll("li[data-i]");
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        activeIdx = (activeIdx + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items.forEach((li, i) => li.classList.toggle("active", i === activeIdx));
      } else if (e.key === "Enter") {
        e.preventDefault();
        pickSuggestion(activeIdx >= 0 ? activeIdx : 0);
      } else if (e.key === "Escape") {
        hideSuggestions();
      }
    });
    els.suggestions.addEventListener("mousedown", (e) => {
      const li = e.target.closest("li[data-i]");
      if (li) { e.preventDefault(); pickSuggestion(+li.dataset.i); }
    });
    els.search.addEventListener("blur", () => setTimeout(hideSuggestions, 150));

    els.places.addEventListener("click", onPlaceAction);

    document.querySelectorAll("[data-preset]").forEach((b) =>
      b.addEventListener("click", () => {
        state.places = PRESETS[b.dataset.preset].map(([name, en, lat, lng]) => newPlace(name, lat, lng, en));
        state.result = null;
        saveState();
        render();
        fitMap();
      })
    );

    els.mode.addEventListener("change", () => { state.mode = els.mode.value; state.result = null; saveState(); render(); });
    els.roundTrip.addEventListener("change", () => { state.roundTrip = els.roundTrip.checked; state.result = null; saveState(); render(); });
    els.endAtLast.addEventListener("change", () => { state.endAtLast = els.endAtLast.checked; state.result = null; saveState(); render(); });

    els.optimize.addEventListener("click", () => plan(null));
    els.replan.addEventListener("click", replanFromHere);

    $("#share").addEventListener("click", async () => {
      const url = shareUrl();
      try { await navigator.clipboard.writeText(url); setStatus(t("copied")); }
      catch (e) { window.prompt(t("share"), url); }
    });
    $("#clear").addEventListener("click", () => {
      if (!state.places.length || !confirm(t("confirmClear"))) return;
      state.places = [];
      state.result = null;
      saveState();
      render();
    });
  }

  initMap();
  bind();
  render();
  fitMap();
})();
