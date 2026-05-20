# KR DayPilot GitHub Pages 배포

## 배포 방식

이 배포는 서버를 24시간 켜두지 않는 무료 개인용 배포 방식이다.

- GitHub Actions가 장 마감 이후 데이터 갱신과 추천 산출을 실행한다.
- `kr_precision_backtest.build_static_site`가 정적 HTML/JS/JSON 산출물을 만든다.
- GitHub Pages가 정적 대시보드를 호스팅한다.
- 관심종목, 제외, 메모, paper ledger는 서버가 아니라 브라우저 `localStorage`에 저장된다.

## 최초 설정

1. GitHub 저장소의 `Settings > Pages`로 이동한다.
2. `Build and deployment`의 Source를 `GitHub Actions`로 설정한다.
3. `Actions` 탭에서 `Build and deploy KR DayPilot Pages` workflow를 수동 실행한다.
4. 실행이 끝나면 workflow의 `deploy` job 또는 저장소 Pages 설정에 배포 URL이 표시된다.

## 선택 Secrets

저장소 `Settings > Secrets and variables > Actions`에 아래 값을 넣으면 데이터 커버리지가 좋아진다.

| Secret | 용도 | 없을 때 |
|---|---|---|
| `OPENDART_API_KEY` | DART 재무/공시 보강 | KRX 밸류에이션 중심으로 제한 |
| `KRX_ID` | KRX 인증 수급/공매도 데이터 | 해당 데이터 수집 제한 |
| `KRX_PW` | KRX 인증 수급/공매도 데이터 | 해당 데이터 수집 제한 |

실전 주문 관련 키는 이 배포에 넣지 않는다. GitHub Pages 버전은 paper review 전용이다.

## 운영 방식

- 자동 갱신: 평일 18:37 KST에 실행된다.
- 즉시 갱신: GitHub Actions에서 `Run workflow`를 누른다.
- 개인 메모/ledger 백업: 브라우저 저장소 기반이므로, 브라우저를 바꾸거나 캐시를 지우면 사라질 수 있다.

## 로컬 검증

```powershell
python -m kr_precision_backtest.build_static_site --output site
python -m http.server 8780 --directory site
```

브라우저에서 `http://127.0.0.1:8780/`을 연다.

## 한계

- GitHub Pages는 정적 호스팅이므로 서버 API와 실시간 데이터 갱신 버튼은 동작하지 않는다.
- 장중 실시간 판단은 증권사 앱의 현재가와 뉴스 확인이 우선이다.
- 추천 결과는 매수 확정 신호가 아니라 paper review용 의사결정 자료다.
