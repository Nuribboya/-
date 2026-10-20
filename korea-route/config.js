/*
 * 사이트 설정.
 *
 * kakaoJsKey: 카카오 developers 의 "JavaScript 키".
 *   - 브라우저에 그대로 노출되는 키라서 저장소에 올려도 된다. 대신 카카오 콘솔에서
 *     "플랫폼 > Web > 사이트 도메인"에 등록한 도메인에서만 동작한다.
 *   - REST API 키나 Admin 키는 절대 여기에 넣지 말 것 (노출되면 남이 쓸 수 있음).
 *   - 비워두면 카카오 검색 없이 OpenStreetMap 검색만 사용한다.
 */
window.APP_CONFIG = {
  kakaoJsKey: "",
};
