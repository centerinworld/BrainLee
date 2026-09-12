# CEO Briefing Mobile

Expo 기반 모바일 MVP입니다.

## 목적

- 관리자 콘솔과 같은 화면 구조를 유지
- 로그인 역할에 따라 접근 가능한 페이지만 노출
- `admin`, `ceo`, `staff` 권한별로 같은 컴포넌트를 재사용

## 데모 계정

- `admin / 4000`
- `ceo / 2000`
- `staff / 3000`

## 실행

```powershell
cd C:\Users\LEE\Documents\codex\ceo-briefing-platform\mobile-app
npm install
npx expo start
```

## 로컬 서버 연결

휴대폰 Expo Go에서 접속할 때는 `127.0.0.1` 대신 백엔드가 실행 중인 PC의 로컬 IP를 입력해야 합니다.

예시:

- `http://192.168.0.15:8011`

## 현재 범위

- 로그인
- 역할별 페이지 제한
- 일정 조회
- 승인된 기사 조회

## 다음 단계 추천

1. Firebase Cloud Messaging 연결
2. 관리자 승인 기사 푸시 발송
3. 실제 사용자 계정 관리 화면 추가
