#!/bin/bash
# 프런트 배포(2026-09-27): 새로 빌드하되 **이전 빌드의 해시 파일을 assets/에 남겨** 이미 열려 있던 탭이 옛 화면 조각을 계속 불러올 수 있게 한다.
# (배포 직후 "수급 현황 화면 에러"의 원인: 옛 탭이 삭제된 조각 파일을 요청)  사용: bash scripts/deploy_frontend.sh
set -euo pipefail
cd "$(dirname "$0")/../frontend"
rm -rf dist_new
npx vite build --outDir dist_new
if [ -d dist/assets ]; then cp -n dist/assets/* dist_new/assets/ 2>/dev/null || true; fi   # 새 파일과 이름이 같지 않은 옛 파일만 보존
rm -rf dist_prev && [ -d dist ] && mv dist dist_prev
mv dist_new dist
# 보존 파일이 무한히 쌓이지 않도록 30일 지난 것은 정리
find dist/assets -type f -mtime +30 -delete 2>/dev/null || true
echo "배포 완료: $(ls dist/assets | wc -l | tr -d ' ')개 파일"
