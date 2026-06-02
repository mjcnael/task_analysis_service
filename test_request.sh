#!/usr/bin/env bash
# Тестовый запрос для проверки создания задачи через эндпоинт формы.
# Использование:
#   ./test_request.sh                           # по умолчанию http://localhost:8000
#   ./test_request.sh http://192.168.1.10:8000  # явный адрес

set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"

PAYLOAD=$(cat <<'JSON'
{
  "ФИО Внедренца": "Иванов Иван Иванович",
  "Номер наряда из К7": "K7-12345",
  "Офис": "Владивосток",
  "Менеджер по наряду": "Петров Пётр Петрович",
  "Клиент:": "ООО Ромашка",
  "Задачи:": "Задача №1\nЗадача №2\nЗадача №3"
}
JSON
)

echo "=== Цель: $BASE_URL ==="
echo

echo "--- 1) Проверка /status ---"
curl -sS "$BASE_URL/status"
echo; echo

echo "--- 2) POST /receive (JSON в теле) ---"
curl -sS -X POST "$BASE_URL/receive" \
    -H 'Content-Type: application/json' \
    -d "$PAYLOAD"
echo; echo

echo "--- 3) GET /receive/<urlencoded JSON> (как Яндекс Формы) ---"
ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$PAYLOAD")
curl -sS "$BASE_URL/receive/$ENC"
echo; echo

echo "Готово. Если bitrix_task_id заполнен — задача создана в Bitrix24."
echo "Если ok:false — смотрите поле message и логи сервиса: docker compose logs -f main"
