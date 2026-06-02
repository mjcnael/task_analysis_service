# Task Analysis Service

Сервис принимает ответы Яндекс Формы «Челлендж ОВ» и создаёт задачи в **Bitrix24**
в указанном проекте (рабочей группе), назначая ответственным менеджера по наряду,
указанного в форме. Менеджер ищется в Bitrix24 по ФИО через REST API.

Требования: Ubuntu 20.04+, Docker и Docker Compose v2.

### 1. Установка Docker

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
# перелогиньтесь, чтобы группа docker применилась
```

### 2. Развёртывание

```bash
git clone <ваш-репозиторий> task_analysis_service
cd task_analysis_service
git checkout feature/bitrix24-integration
docker compose up -d --build
```

Сервис стартует на порту **8000**:

```bash
curl http://localhost:8000/status
# {"status":"ok","bitrix_initialized":false}
```

Логи:
```bash
docker compose logs -f main
```

### 3. Настройка через веб-интерфейс

Откройте `http://<адрес-сервера>:8000/` — будет редирект в настройки.
