---
name: lecture-recording-organizer
description: Rename Apple Voice Memos lecture recordings and attach them to matching Apple Notes by date and course, including lightweight transcription checks and bounded retries. Use for this lecture-recording workflow only.
---

# 강의 녹음 정리 및 메모 첨부

읽기와 판정은 로컬 DB와 AppleScript로 먼저 끝내고, Computer Use는 Voice Memos 제목 변경과 Notes 첨부 제목 변경·전사 시작처럼 실제 UI 조작이 필요한 순간에만 쓴다. Asia/Seoul 기준이며 2026-09-01부터 2026-12-18까지만 실행한다.

오디오를 재생하지 않는다. Safari·Finder 미리보기·iPhone 미러링을 사용하지 않는다. Voice Memos 원본과 Notes 메모 자체는 삭제하지 않는다. Voice Memos에서는 「모든 녹음 항목」만 다루며 휴지통·최근 삭제 항목·다른 폴더는 열지 않는다.

## 시간표와 이름

- 월 09:00 미주지역지리, 13:00 지도학및실습, 16:30 도시지리학특강
- 화 15:00 응용 지형학, 16:30 기후변화와 미래환경
- 수 10:30 미주지역지리, 13:00 지도학및실습, 15:00 도시지리학특강
- 목 15:00 응용 지형학, 16:30 기후변화와 미래환경
- 금요일 수업 없음

시간표 판정은 반드시 Voice Memos DB의 `ZDATE` 시작 시각으로만 한다. 파일 생성·수정·종료 시각이나 `종료 시각-재생시간`은 쓰지 않는다. 가장 가까운 수업 시작과 ±15분 이내에서 유일하게 맞을 때만 처리한다.

표준 이름은 `M/D 과목명` 또는 `M/D 과목명 1/n`이다. 제목이 「새로운 녹음…」이 아니어도 표준 형식이 아니면 후보로 삼는다. 같은 실행에 같은 날짜·과목 녹음이 여러 개면 시작 시각 순으로 `1/n`, `2/n`을 붙인다. 나중에 기존 분할 번호를 다시 맞추지는 않는다.

## 빠른 판정

Voice Memos DB를 읽기 전용으로 조회한다.

`~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/CloudRecordings.db`

```sql
SELECT Z_PK, ZCUSTOMLABELFORSORTING, ZPATH,
       datetime(ZDATE + 978307200, 'unixepoch', 'localtime') AS started_local,
       ZDURATION
FROM ZCLOUDRECORDING
WHERE ZPATH IS NOT NULL AND ZDURATION > 0
ORDER BY ZDATE DESC;
```

평상시 자동 실행은 오늘 녹음과 이전 실행에서 보류한 항목만 사용한다. DB 결과 전체를 UI에서 다시 훑지 않는다. 이름 변경 직전 후보의 `ZDURATION`과 파일 크기를 3초 간격으로 한 번 재확인한다. 변하면 아직 녹음 중이므로 다음 실행으로 넘긴다. 이름을 바꿀 후보만 「모든 녹음 항목」에 실제로 보이는지 확인하고 Computer Use로 제목을 바꾼다.

이름 변경 직후 같은 `Z_PK`의 `ZCUSTOMLABELFORSORTING`만 DB에서 다시 읽어 표준 이름과 정확히 같은지 확인한다. 다르면 해당 항목을 UI에서 다시 찾아 이름 변경을 한 번만 재시도하고 같은 방식으로 재확인한다. 두 번째 확인도 실패하면 성공으로 간주하지 말고 Notes에 첨부하지 않은 채 `이름 변경 실패`로 보고한다. 이 검증 때문에 다른 녹음이나 메모를 다시 훑지 않는다.

재생시간은 수업 판정이 아니라 녹음 완료 확인과 Notes 중복 판정에만 쓴다.

## Notes 첨부

과목→Notes 폴더는 다음과 같다: 미주지역지리→미주지역지리, 지도학및실습→지도학, 도시지리학특강→도시지리특강, 응용 지형학→응용지형학, 기후변화와 미래환경→기후변화.

AppleScript로 iCloud의 해당 과목 폴더와 제목이 정확히 `M/D`인 메모를 찾는다. 과목 폴더가 「강의 녹음」 아래가 아니거나 같은 `M/D` 메모가 0개 또는 2개 이상이면 추측하지 않고 해당 녹음만 건너뛰어 보고한다. 메모를 새로 만들지 않는다.

Notes DB는 읽기 전용으로 사용한다.

`~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite`

AppleScript note id 끝의 `p숫자`가 `ZNOTE` 값이다. 현재 첨부는 `Z_ENT=5`, `ZTYPEUTI='com.apple.m4a-audio'`, `ZMARKEDFORDELETION=0`, `ZUSERTITLE IS NOT NULL`인 행만 센다. 제목은 `/`와 `:` 및 확장자 차이를 정규화하고, 재생시간은 초 단위로 반올림한다. 정규화 제목과 재생시간이 모두 같을 때만 같은 녹음으로 인정한다.

- 정확히 일치하는 첨부가 없으면 AppleScript의 `make new attachment ... with data`로 원본 `.m4a`를 해당 메모에 직접 첨부한다. 파일 선택창이나 드래그는 쓰지 않는다.
- 새 첨부가 UI에서 「오디오」로 보이면 그 첨부 한 개만 Computer Use로 `M/D 과목명`으로 바꾼다.
- 같은 제목의 짧은 부분 첨부가 있어도 원본 재생시간과 다르면 완성본을 별도로 첨부하고 불일치를 보고한다.

## 전사 확인

대상 첨부 행의 `length(ZADDITIONALINDEXABLETEXT)>0`이고 `ZNEEDSTRANSCRIPTION=0`이면 전사 완료다. 화면에서 전사문 전체를 읽지 않는다.

전사문이 없으면 대상 첨부만 더블클릭해 패널을 열고 `전사문 및 요약을 봅니다` 버튼을 누른다. `전사 중…`을 확인한 뒤 DB를 10초 간격, 최대 60초만 확인하고 전사문이 생기면 즉시 끝낸다. 재생 버튼은 누르지 않는다.

60초 뒤에도 명시적 실패가 아니면 `전사 대기`로 남기고 다음 실행에서 그 항목만 재확인한다. 다음 실행에서도 새 복사본을 만들지 말고 같은 첨부의 전사 상태 확인과 전사 유도만 반복한다. 명시적 실패라면:

- 사용자가 있는 실행은 삭제 직전 확인을 받은 뒤 실패한 Notes 첨부 하나만 삭제하고 재첨부할 수 있다. 삭제와 재첨부를 포함한 전사 시도는 최대 5회까지만 한다.
- 무인 예약 실행은 첨부를 삭제하거나 같은 원본을 중복 첨부하지 않는다. 실패한 첨부를 그대로 두고 `전사 실패—수동 재첨부 필요`로 보고한다.

어느 경우에도 Voice Memos 원본이나 Notes 메모를 삭제하지 않는다. 하나라도 전사되면 즉시 완료하고 추가 복사본을 만들지 않는다.

## 보고

이름 변경, 새 첨부, 이미 완료, 부분 첨부 불일치, 전사 완료·대기·실패, 폴더/메모 누락만 짧게 보고한다. 오디오 내용이나 전사문은 보고에 포함하지 않는다.
