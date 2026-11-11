# Сервис сотрудников

Отдельный переиспользуемый backend-сервис для основных данных сотрудников и проверки по лицу.

## Что предоставляет сервис

- Каталог цехов и отделов
- API основных данных сотрудников
- API проверки по лицу
- API обнаружения области лица
- Управление исключениями для проверки по лицу
- Схема OpenAPI по адресу `/api/schema/`
- Swagger UI по адресу `/api/docs/`
- ReDoc по адресу `/api/redoc/`
- Подробная интеграционная документация в файле `API_INTEGRATION.md`

Внешняя интеграция через `X-Employee-Service-Key` или `Authorization: Bearer <key>` работает только в режиме чтения. Для такого доступа разрешены только `GET`, `HEAD` и `OPTIONS`.

## Основные эндпоинты

- `GET /api/v1/departments/`
- `GET /api/v1/departments/<id>/`
- `GET /api/v1/sections/`
- `GET /api/v1/sections/<id>/`
- `GET /api/v1/employees/`
- `GET /api/v1/employees/<slug>/`

## Аутентификация

Передайте один из настроенных API-ключей одним из способов:

- `X-Employee-Service-Key: <key>`
- `Authorization: Bearer <key>`

Сервисный API-ключ не даёт права на создание, изменение или удаление данных сотрудников из другой системы.

Для быстрого просмотра и импорта в другие системы используйте:

- Swagger UI: `/api/docs/`
- ReDoc: `/api/redoc/`
- OpenAPI schema: `/api/schema/`
- Пошаговую интеграцию: `API_INTEGRATION.md`
- Готовый Postman collection: `postman/employee_service_readonly_collection.json`
- Готовый Postman environment: `postman/employee_service_readonly_environment.json`

## Импорт сотрудников из Excel

Во внутреннем веб-интерфейсе на главной странице доступна кнопка `Импорт Excel`. Она предназначена для быстрого массового добавления сотрудников.

Правила импорта:

- поддерживаются файлы `.xlsx` и `.xls`
- если хотя бы одна строка содержит ошибку, весь импорт отменяется
- `Табельный номер` обязателен и должен быть уникальным
- для табельных номеров с ведущими нулями колонку лучше хранить как текст
- названия колонок можно указывать по-русски или использовать системные имена полей

Обязательные колонки:

- `Табельный номер` или `tabel_number`
- `Имя` или `first_name`
- `Фамилия` или `last_name`

Поддерживаемые дополнительные колонки:

Ниже перечислены поля сверх обязательных `Имя` (`first_name`) и `Фамилия` (`last_name`):

- `Отчество` или `surname`
- `Пол` или `gender` (`M`/`F`, `М`/`Ж`, `мужской`/`женский`)
- `Дата рождения` или `date_of_birth`
- `Рост` или `height`
- `Размер одежды` или `clothe_size`
- `Халат` или `clothe_size`
- `Размер обуви` или `shoe_size`
- `Телефон 1` или `phone_number_1`
- `Телефон 2` или `phone_number_2`
- `Серия паспорта` или `passport_series`
- `Номер паспорта` или `passport_number`
- `ЖШШИР` или `jshshir`
- `Цех` или `department_name`
- `Руководитель цеха` или `boss_full_name`
- `Отдел` или `section_name`
- `Должность` или `position`
- `Дата приема на работу` или `date_of_employment`
- `Дата изменения должности` или `date_of_change_position`

Особенности:

- при передаче `Цех` и `Отдел` сервис сам создаст недостающие записи
- для каждого импортированного сотрудника автоматически создаётся связанный пользователь Django
- логин формируется из имени и фамилии; при совпадении сервис добавляет суффикс для уникальности
- пароль генерируется автоматически и возвращается в результате успешного импорта
- роль связанного пользователя всегда назначается как `user`, даже если в Excel передано другое значение
- поля `Активен`, `Источник системы`, `Внешний ID` и `Метаданные` через Excel-import не задаются и берутся из стандартных значений сервиса
- даты лучше передавать в формате `YYYY-MM-DD`

## Импорт фотографий сотрудников из папки

Если сотрудники уже созданы, фотографии можно массово привязать отдельной командой. Имя файла должно совпадать с табельным номером сотрудника.

Примеры:

```powershell
& ".venv\Scripts\python.exe" manage.py import_employee_images "C:\images\employees"
& ".venv\Scripts\python.exe" manage.py import_employee_images "C:\images\employees" --overwrite
& ".venv\Scripts\python.exe" manage.py import_employee_images "C:\images\employees" --recursive
```

Правила:

- поддерживаются `.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.gif`
- `12345.jpg` будет привязан к сотруднику с `tabel_number=12345`
- без `--overwrite` сотрудники с уже загруженным фото будут пропущены
- с `--recursive` команда просматривает вложенные папки

## Локальный запуск

```powershell
& "c:/Users/shabonov.m/Desktop/TB project/.venv/Scripts/python.exe" -m pip install -r employee_service/requirements.txt
& "c:/Users/shabonov.m/Desktop/TB project/.venv/Scripts/python.exe" employee_service/manage.py migrate
& "c:/Users/shabonov.m/Desktop/TB project/.venv/Scripts/python.exe" employee_service/manage.py runserver 0.0.0.0:8010
```

## Переменные окружения

- `EMPLOYEE_SERVICE_SECRET_KEY`
- `EMPLOYEE_SERVICE_DEBUG`
- `EMPLOYEE_SERVICE_ALLOWED_HOSTS`
- `EMPLOYEE_SERVICE_API_KEYS`
- `EMPLOYEE_SERVICE_DB_ENGINE`
- `EMPLOYEE_SERVICE_DB_NAME`
- `EMPLOYEE_SERVICE_DB_USER`
- `EMPLOYEE_SERVICE_DB_PASSWORD`
- `EMPLOYEE_SERVICE_DB_HOST`
- `EMPLOYEE_SERVICE_DB_PORT`
- `FACE_SIMILARITY_THRESHOLD`

## Docker + host PostgreSQL

Текущая docker-конфигурация `employee_service` подключается не к отдельному контейнеру PostgreSQL, а к PostgreSQL, который уже работает на сервере и виден в pgAdmin.

Основные значения находятся в [employee_service/.env](employee_service/.env):

- `EMPLOYEE_SERVICE_DB_HOST=192.168.101.6`
- `EMPLOYEE_SERVICE_DB_PORT=5432`
- `EMPLOYEE_SERVICE_DB_NAME=employee_data`

Если контейнер не может подключиться к серверному PostgreSQL, на Linux-хосте нужно включить приём соединений от Docker bridge и других приватных Docker-подсетей. Для Ubuntu/Debian можно выполнить:

```bash
cd employee_service
sudo bash ./configure_host_postgres.sh
docker compose down
docker compose up -d --build
```

Скрипт делает следующее:

- выставляет `listen_addresses = 'localhost,192.168.101.6'` в `postgresql.conf`, чтобы и локальный pgAdmin, и Docker-контейнеры могли подключаться
- добавляет правила `pg_hba.conf` для приватных диапазонов Docker: `127.0.0.1/32`, `172.16.0.0/12`, `192.168.0.0/16`, `10.0.0.0/8`
- перезапускает PostgreSQL

Если у сервера другой IP или другая версия PostgreSQL, запустите так:

```bash
sudo POSTGRES_VERSION=16 POSTGRES_LISTEN_ADDRESS='localhost,192.168.101.6' bash ./configure_host_postgres.sh
```

## Интеграция с основным backend

Задайте эти переменные для существующего PPE backend:

- `EMPLOYEE_SERVICE_ENABLED=true`
- `EMPLOYEE_SERVICE_BASE_URL=http://127.0.0.1:8010`
- `EMPLOYEE_SERVICE_API_KEY=dev-employee-service-key`
- `EMPLOYEE_SERVICE_TIMEOUT=15`

После включения основной backend будет:

- синхронизировать сотрудников с этим сервисом при создании, импорте и редактировании
- сначала отправлять сюда запросы проверки по лицу
- сначала отправлять сюда запросы обнаружения области лица
- сначала отправлять сюда управление исключениями проверки по лицу