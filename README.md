# neuro-sarc-daily-alert

PubMed 기반으로 **신경재생/가소성 + 사르코페니아 + 의료 AI** 논문을 수집하고,
OpenAI로 2~3개 핵심 bullet 요약을 생성해 이메일로 발송하는 자동화 스크립트입니다.

## 주요 개선 사항
- 요약을 논문별 1회만 생성하여 중복 호출/쿼터 소모를 줄였습니다.
- OpenAI 출력을 강제 정규화해 `**`, 깨진 bullet, 서론 문구가 이메일에 노출되지 않게 처리했습니다.
- API rate limit/ quota 초과 시 재시도 후, 실패하면 abstract 기반 fallback 요약으로 대체합니다.
- 월/수/토 스케줄(옵션)을 코드와 GitHub Actions에서 함께 지원합니다.
- 의료 AI/뇌 전기자극 관련 별도 섹션을 추가했습니다.

## 환경 변수
- `OPENAI_API_KEY`
- `GMAIL_USER`
- `GMAIL_APP_PASSWORD`
- `NCBI_EMAIL`
- `TO_EMAIL` (기본값: `rhhyun@gmail.com`)

선택값:
- `OPENAI_MODEL` (기본 `gpt-4.1-mini`)
- `MAX_RESULTS_NEURAL` (기본 5)
- `MAX_RESULTS_SARC` (기본 5)
- `MAX_RESULTS_AI` (기본 3)
- `SUMMARY_DELAY_SECONDS` (기본 8)
- `SUMMARY_BATCH_SIZE` (기본 5, 배치 요약 단위)
- `ENFORCE_MWS_SCHEDULE` (`true`일 때 월/수/토만 실행)

## 실행
```bash
pip install -r requirements.txt
python pubmed_alert.py
```
