# 인테리어·건설 Daily Briefing

공개된 건설·건축 뉴스, FRED·IMF 글로벌 원자재 월별 가격, 조달청 시설공통자재 참고가격, 공정거래위원회 브랜드 인테리어 비용을 모아 보여주는 Streamlit 대시보드입니다.

## GitHub에 올리는 방법

1. `INTWEB` 저장소에서 **Add file → Upload files**를 누릅니다.
2. 이 폴더 안의 `app.py`, `requirements.txt`, `README.md` 세 파일을 업로드합니다.
3. **Commit changes**를 누릅니다.
4. Streamlit의 Main file path를 `app.py`로 설정합니다.

## 공공데이터 인증키 설정

GitHub에는 인증키를 올리지 않습니다. Streamlit 앱의 **Settings → Secrets**에 아래 형식으로 저장합니다.

```toml
DATA_GO_KR_KEY = "공공데이터포털_Decoding_인증키"
```

`조달 자재가격` 탭은 검색 버튼을 눌렀을 때만 API를 호출하며, 같은 검색은 1시간 동안 캐시됩니다.
같은 인증키로 `브랜드 인테리어비` 탭도 조회할 수 있습니다.

원자재 자료가 일시적으로 실패하면 화면에 품목별 연결 진단과 `원자재 다시 불러오기` 버튼이 표시됩니다.

## 공개 범위

공개 데이터만 사용하도록 설계했습니다. 회사 내부 견적, 협력사 정보, 점포명, 계약금액 등 비공개 정보는 입력하지 마세요.
