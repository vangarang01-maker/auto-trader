#!/bin/bash
# 매일 07:30 (월~금) run.py 자동 실행 cron 등록

PYTHON=$(which python3)
DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DIR/logs/daily.log"

CRON_ENTRY="30 7 * * 1-5 cd $DIR && $PYTHON $DIR/run.py >> $LOG 2>&1"

# 기존 항목 제거 후 재등록
(crontab -l 2>/dev/null | grep -v "auto-trader/run.py"; echo "$CRON_ENTRY") | crontab -

echo "cron 등록 완료:"
echo "  $CRON_ENTRY"
echo ""
echo "확인: crontab -l"
