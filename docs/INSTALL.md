# Постоянная установка Windows

Релиз устанавливается отдельно от исходников. Рабочая папка проекта и ее `.venv` после установки серверу не нужны. Существующие настройки, история заданий и секреты не перемещаются.

Структура:

```text
%LOCALAPPDATA%\BambuWorkflow\app\0.2.0\
  .venv\Scripts\python.exe
  tools\call_mcp.py
  tools\install.ps1
  docs\
  skills\bambu-print\SKILL.md
```

Python 3.12+ должен быть установлен вне рабочей папки. Установщик создает новое окружение на месте, не копирует старую `.venv`. Bambu Studio и Blender MCP устанавливаются пользователем отдельно и не входят в пакет.

## Установка релиза

Распакуйте release ZIP. В PowerShell из распакованного каталога выполните:

```powershell
& .\scripts\install.ps1 -Python 'C:\Path\To\Python\python.exe' -InstallSkill
```

По умолчанию зависимости скачиваются из PyPI. `requirements.lock.txt` фиксирует проверенные версии зависимостей для Windows. Проектный wheel проверяется по `SHA256SUMS.txt`. Для установки без сети заранее подготовьте каталог совместимых wheel и передайте `-Wheelhouse 'C:\Path\To\wheelhouse'`.

Установщик выводит постоянный путь Python и число MCP-инструментов. Проверка выполняется без обращения к принтеру. Конфигурацию Codex установщик не меняет.

Если политика PowerShell запрещает запуск скрипта, можно разрешить только эту проверенную копию:

```powershell
Unblock-File -LiteralPath '.\scripts\install.ps1'
```

## Первичная настройка нового пользователя

```powershell
$runtime = Join-Path $env:LOCALAPPDATA 'BambuWorkflow\app\0.2.0\.venv\Scripts\python.exe'
& $runtime -m bambu_workflow.cli configure
& $runtime -m bambu_workflow.cli install-codex
```

`configure` спрашивает адрес, серийный номер, LAN-код, установленное сопло, пластину и путь Bambu Studio. LAN-код сохраняется в системном хранилище секретов. Для нескольких устройств добавьте отдельные записи с уникальными `id` в конфигурацию.

Режим принтера и прошивка автоматически не меняются. Действия для LAN Developer Mode описаны в `SETUP.md`.

## Установка из Git

После клонирования репозитория соберите wheel и передайте его тому же установщику. Временное окружение сборки не используется постоянным MCP:

```powershell
git clone https://github.com/didyp3073-oss/bambu-workflow-mcp.git bambu-workflow-mcp
Set-Location bambu-workflow-mcp
python -m venv .build
& .\.build\Scripts\python.exe -m pip install build
& .\.build\Scripts\python.exe -m build --wheel
& .\scripts\install.ps1 -WheelPath '.\dist\bambu_workflow_mcp-0.2.0-py3-none-any.whl' `
  -Python '.\.build\Scripts\python.exe' -InstallSkill
```

При новой версии укажите соответствующее имя wheel. Установщик берет базовый Python временного окружения и создает новый runtime вне клонированной папки. Открытый проект, его `.build` и исходники после этого не нужны для запуска установленного сервера.

## Обновление существующей установки

Каждая версия получает отдельный каталог. Старый runtime сохраняется для отката. Укажите существующую конфигурацию явно:

```powershell
& .\scripts\install.ps1 -Python 'C:\Path\To\Python\python.exe' `
  -ConfigPath 'C:\Path\To\Existing\BambuWorkflow\config.json' -InstallSkill
```

После успешной проверки замените только `command` сервера `bambu_workflow` в Codex на выведенный Python. Аргументы: `["-m", "bambu_workflow.server"]`. Сохраните `env.BAMBU_WORKFLOW_CONFIG` со старым абсолютным путем. Секреты не добавляйте в регистрацию MCP.

Если запись `bambu_workflow` уже существует с другим Python, команда `install-codex` откажется молча заменять ее. Измените эту конкретную запись, сохранив остальные серверы. Новые инструменты появятся после перезагрузки MCP или в новом чате.

Повторная установка того же wheel безопасно возвращает существующий runtime. После прерванной установки используйте `-Resume`. Wheel с другим содержимым требует нового номера версии.

`-InstallSkill` устанавливает `bambu-print` в `$CODEX_HOME\skills`, либо `%USERPROFILE%\.codex\skills`. Для клиента с каталогом `%USERPROFILE%\.agents\skills` передайте `-SkillRoot (Join-Path $env:USERPROFILE '.agents\skills')`. Выберите один каталог, который читает ваш Codex. Существующая копия сначала сохраняется в соседний `skill-backups`, вне каталога обнаружения навыков. Без этого ключа skill остается только внутри установленного релиза.

## MCP из Python

По умолчанию пример только перечисляет инструменты. Он отключает восстановление старых workers при диагностическом запуске сервера:

```powershell
$runtime = Join-Path $env:LOCALAPPDATA 'BambuWorkflow\app\0.2.0\.venv\Scripts\python.exe'
$client = Join-Path $env:LOCALAPPDATA 'BambuWorkflow\app\0.2.0\tools\call_mcp.py'
& $runtime $client --config 'C:\Path\To\BambuWorkflow\config.json'
```

Подготовка коллекции `Print` без запуска:

```powershell
$payload = '{"request_id":"my-design-001","source":"blender","selection":{"collection":"Print"},"user_prompt":"Подготовь модель из коллекции Print к печати"}'
& $runtime $client --tool prepare_print --arguments $payload --config 'C:\Path\To\BambuWorkflow\config.json'
```

Для печати используйте явный `--tool print_model` и исходное поручение пользователя. `request_id` обязателен и остается одинаковым при повторе того же запроса. Для чтения задания используйте `--tool job_status --arguments '{"job_id":"ID_FROM_RESPONSE","reconcile":false}'`. `reconcile:true` получает свежую телеметрию принтера.

## Откат и удаление runtime

Для отката верните `command` Codex на Python прежней версии. Конфигурация и база заданий остаются прежними. Не запускайте две копии одного задания.

Чтобы удалить только конкретную версию приложения, сначала уберите ссылки Codex на нее и нормально завершите ее MCP/workers. Затем из релиза выполните:

```powershell
& .\scripts\install.ps1 -Uninstall -Version 0.2.0 -WhatIf
& .\scripts\install.ps1 -Uninstall -Version 0.2.0
```

Удаление проверяет marker владельца и расположение каталога, отказывается работать при активных процессах этой версии. Конфигурация, база, артефакты заданий, другие версии и системное хранилище секретов сохраняются.
