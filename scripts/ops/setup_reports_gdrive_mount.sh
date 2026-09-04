#!/bin/bash
# setup_reports_gdrive_mount.sh
# 업로드 완료 후 실행: 구글 드라이브 마운트 + 로컬 reports 삭제
# 사용법: bash setup_reports_gdrive_mount.sh

set -e

REPORTS_LOCAL="/Volumes/Realtek_NVME/stock_dashboard/runtime/reports"
MOUNT_POINT="/Users/brainlee/gdrive_reports"
GDRIVE_REMOTE="gdrive:stock_reports"
PLIST_PATH="$HOME/Library/LaunchAgents/com.stock-dashboard.gdrive-reports.plist"

echo "=== 1. 업로드 완료 확인 ==="
LOCAL_COUNT=$(find "$REPORTS_LOCAL" -type f | wc -l | tr -d ' ')
REMOTE_COUNT=$(rclone ls "$GDRIVE_REMOTE" 2>/dev/null | wc -l | tr -d ' ')
echo "로컬: $LOCAL_COUNT 파일 / 구글 드라이브: $REMOTE_COUNT 파일"

if [ "$REMOTE_COUNT" -lt $(( LOCAL_COUNT * 95 / 100 )) ]; then
    echo "❌ 업로드 미완료 (구글 드라이브 파일 수가 로컬의 95% 미만)"
    echo "   rclone 진행 로그: tail -f /tmp/rclone_upload.log"
    exit 1
fi
echo "✅ 업로드 완료 확인"

echo ""
echo "=== 2. macFUSE 설치 확인 ==="
if ! command -v mount_macfuse &>/dev/null && ! ls /Library/Filesystems/macfuse.fs &>/dev/null 2>&1; then
    echo "⚠️  macFUSE가 없습니다. 설치 필요:"
    echo "    brew install --cask macfuse"
    echo "    (설치 후 시스템 환경설정 > 보안 > 허용 필요)"
    exit 1
fi

echo ""
echo "=== 3. 마운트 포인트 생성 ==="
mkdir -p "$MOUNT_POINT"

echo ""
echo "=== 4. LaunchAgent plist 생성 (자동 마운트) ==="
cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.stock-dashboard.gdrive-reports</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/rclone</string>
        <string>mount</string>
        <string>gdrive:stock_reports</string>
        <string>$MOUNT_POINT</string>
        <string>--vfs-cache-mode</string>
        <string>full</string>
        <string>--vfs-cache-max-size</string>
        <string>2G</string>
        <string>--dir-cache-time</string>
        <string>72h</string>
        <string>--log-file</string>
        <string>/tmp/rclone_mount.log</string>
        <string>--log-level</string>
        <string>INFO</string>
        <string>--daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/gdrive_mount_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/gdrive_mount_stderr.log</string>
</dict>
</plist>
EOF

echo "✅ plist 생성: $PLIST_PATH"

echo ""
echo "=== 5. LaunchAgent 로드 (마운트 시작) ==="
launchctl load "$PLIST_PATH"
sleep 5

if mount | grep "$MOUNT_POINT" > /dev/null 2>&1; then
    echo "✅ 마운트 성공: $MOUNT_POINT"
else
    echo "⚠️  마운트 확인 중... (--daemon 모드라 잠시 기다립니다)"
    sleep 10
    ls "$MOUNT_POINT" | head -5 && echo "✅ 마운트 정상" || echo "❌ 마운트 실패 — /tmp/rclone_mount.log 확인"
fi

echo ""
echo "=== 6. 심볼릭 링크 교체 (reports → 마운트 포인트) ==="
mv "$REPORTS_LOCAL" "${REPORTS_LOCAL}_local_backup_$(date +%Y%m%d)"
ln -s "$MOUNT_POINT" "$REPORTS_LOCAL"
echo "✅ 심볼릭 링크: $REPORTS_LOCAL -> $MOUNT_POINT"

echo ""
echo "=== 7. 백업 폴더 삭제 (12GB 확보) ==="
echo "아래 명령으로 로컬 백업을 삭제하세요:"
echo "    rm -rf ${REPORTS_LOCAL}_local_backup_$(date +%Y%m%d)"
echo "(자동 삭제는 마운트 정상 확인 후 수동으로 실행하세요)"

echo ""
echo "=== 완료 ==="
echo "서버 재시작: launchctl kickstart -k \"gui/\$(id -u)/com.stock-dashboard.local\""
