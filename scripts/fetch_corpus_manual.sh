#!/usr/bin/env bash
# =============================================================================
# Manual fetch helper для документов, недоступных через основной fetch_corpus.py
#
# Запускать с не-curl-blocked сети (обычно — VPN на РФ для российских сайтов,
# которые WAF'ом блочат rapid curl с не-РФ IP).
#
# После запуска — `python scripts/fetch_corpus.py --check` сверит sha256.
# =============================================================================
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CORPUS="$ROOT/data/corpus"
mkdir -p "$CORPUS"
cd "$CORPUS" || exit 1

UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) Gecko/20100101 Firefox/128.0'

fetch() {
  local name=$1 out=$2 url=$3
  echo ">>> $name"
  if curl -fLSs --connect-timeout 60 --max-time 240 -A "$UA" -o "$out" "$url"; then
    local size
    size=$(stat -f '%z' "$out" 2>/dev/null || stat -c '%s' "$out" 2>/dev/null || echo "?")
    echo "[OK]   $name ($size bytes)"
  else
    echo "[FAIL] $name"
    echo "       $url"
    rm -f "$out"
  fi
  sleep 1
}

# Документы, которые российские сайты возвращают только при "правильной" сети.
# Если что-то ниже уже скачано — fetch_corpus.py его пропустит при следующем запуске.

fetch "Газпром AR 2024" \
  "gazprom_ar_2024.pdf" \
  "https://www.gazprom.ru/f/posts/44/479056/gazprom-annual-report-2024-ru.pdf"

# (Минэк, Газпром Accounting, Татнефть — обычно качаются с VPN сразу,
# но если они отсутствуют — раскомментируй блоки ниже.)

# fetch "Минэк СЭР 2026-2028" \
#   "minec_rf_ser_2026-2028.pdf" \
#   "https://www.economy.gov.ru/material/file/bc142016f6ab3772370bb0b4541fc778/prognoz_socialno_ekonomicheskogo_razvitiya_rf_2026-2028.pdf"
#
# fetch "Газпром Accounting 2024" \
#   "gazprom_accounting_2024.pdf" \
#   "https://www.gazprom.ru/f/posts/44/479056/gazprom-accounting-report-2024.pdf"
#
# fetch "Татнефть AR 2024" \
#   "tatneft_ar_2024.pdf" \
#   "https://www.tatneft.ru/uploads/publications/682f3cf820a09408542139.pdf"

echo "---"
echo "Total PDFs in corpus: $(ls -1 "$CORPUS"/*.pdf 2>/dev/null | wc -l | tr -d ' ')"
echo "Recommended next step: python scripts/fetch_corpus.py --check"
