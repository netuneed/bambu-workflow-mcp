# Выбранный путь и протокол

Версия 0.3.0. Источники перепроверены 26.10.2026. Основной маршрут: внешний установленный Bambu Studio CLI, затем LAN MQTT и FTPS. Исходный код слайсера и закрытый сетевой плагин не включаются в пакет. Облачная авторизация и Bambu Connect не используются как доказательство автоматического запуска.

## A1 mini и режим доступа

Официальная история A1 mini указывает `01.05.00.00` от 03.06.2025 как начало Authorization Control и Developer Mode. В обычном режиме неавторизованные команды могут отклоняться при доступной телеметрии. Developer Mode доступен в LAN Mode и отключает проверку команд. Наличие связи, статуса или заявленного флага режима не заменяет аппаратную проверку управления.

Источники: [история прошивок A1 mini](https://wiki.bambulab.com/en/a1-mini/manual/a1-mini-firmware-release-history), [включение Developer Mode](https://wiki.bambulab.com/en/knowledge-sharing/enable-developer-mode), [Bambu blog от 20.01.2025](https://blog.bambulab.com/updates-and-third-party-integration-with-bambu-connect/), [Security White Paper 2025.09](https://cdn1.bambulab.com/trust-center/file/bambulab-security-whitepaper-en.pdf).

Обе wiki были прочитаны прямым HTTPS-запросом после ошибки извлечения через web-инструмент. Страница истории прошивок имела дату обновления 03.06.2026, инструкция режима: 15.10.2025.

Проверенный [официальный BBL registry](https://github.com/bambulab/BambuStudio/blob/79df7ae309be398ed30c9f397c13c9911c2edc57/resources/profiles/BBL.json) содержит 14 моделей, включая H2C, X2D и A2L. Режим и минимальная прошивка не выводятся из одного имени модели. [Authorization Control](https://blog.bambulab.com/firmware-update-introducing-new-authorization-control-system-2/) отдельно ограничивает критические команды, поэтому статус не является проверкой разрешения старта. Четыре dual-tool семейства в этой MCP пока имеют явный блок запуска; таблица находится в [CAPABILITIES.md](CAPABILITIES.md).

## LAN

MQTT использует TLS на 8883, пользователя `bblp`, LAN access code и темы `device/<serial>/request`, `device/<serial>/report`. Свежая полная сводка запрашивается `pushing.command="pushall"`. Pause/resume/stop имеют `print.command` со значениями `pause`, `resume`, `stop`, пустой `param` и отдельный `sequence_id`.

Эти команды видны в [DeviceManager.cpp](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/DeviceManager.cpp). Формат следует сверять с установленной прошивкой. Запись команды в сокет подтверждает передачу, но не выполнение.

FTPS использует implicit TLS на 990 и защищенный data channel. Для прошивок, требующих TLS session reuse, data connection должна использовать сессию control connection. Подготовленный файл получает уникальное имя задания. Контрольная сумма локального снимка сравнивается до отправки и после получения файла с принтера; одна проверка размера недостаточна.

Запуск выбранной пластины обычно использует `print.command="project_file"`, `param="Metadata/plate_1.gcode"`, URL файла на SD и md5 выбранного вложенного G-code. Это протокол сторонних реализаций, а не публичный контракт Bambu. В открытом Studio эти детали переданы закрытому networking plugin. Первичный источник сторонней реализации: [voska/bambu protocol](https://github.com/voska/bambu/blob/main/docs/protocol.md). Открытая граница Studio: [PrintJob.cpp](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/Jobs/PrintJob.cpp), [bambu_networking.hpp](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/Utils/bambu_networking.hpp).

Основной workflow загружает sliced архив под уникальным именем `.3mf`, чтобы использовать `project_file` с явной пластиной и mapping. Расширение локального `prepared.gcode.3mf` не определяет имя на SD. Низкоуровневый адаптер отдельно поддерживает legacy `gcode_file` для имени `.gcode.3mf`, но только для одной пластины с внешней подачей, без project filament selectors. Эта ветка не подтверждена аппаратным запуском новой MCP и не подменяет проверку основного маршрута.

Выбор камеры привязан к описанию устройства: локальные JPEG на TCP 6000 для A1/A1 mini/P1/A2L и отдельный RTSP для других семейств. RTSP не переносится на A1 mini. Snapshot RTSP требует внешнего `ffmpeg`; аппаратно проверен только JPEG A1 mini. Upstream: [MediaPlayCtrl.cpp](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/MediaPlayCtrl.cpp), [printer definitions](https://github.com/bambulab/BambuStudio/tree/master/resources/printers).

## Слайсинг

Официальная [CLI wiki](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage), обновленная 31.03.2026, определяет `--slice 0` как все пластины, `--slice i` как номер пластины i. Рабочая MCP должна проверять фактический `--help` установленной версии.

`--load-settings` и `--load-filaments` требуют полных конфигураций. Ресурсный JSON может содержать только отличия от родителя. В профиле A1 mini также есть `include` для G-code шаблонов. Разрешать только `inherits` недостаточно. [Профиль A1 mini 0.4](https://github.com/bambulab/BambuStudio/blob/master/resources/profiles/BBL/machine/Bambu%20Lab%20A1%20mini%200.4%20nozzle.json).

На macOS исполняемый файл находится в `BambuStudio.app/Contents/MacOS/BambuStudio`. Финальная упаковка копирует ресурсы в `Contents/Resources`, CMake также содержит путь `Contents/resources`. Для BBL выбирается существующий `profiles/BBL` в фактическом bundle. Это подтверждают [BuildMac.sh](https://github.com/bambulab/BambuStudio/blob/master/BuildMac.sh) и [CMakeLists.txt](https://github.com/bambulab/BambuStudio/blob/master/CMakeLists.txt). Наличие бинарного файла и CLI help не доказывает успешный слайсинг на конкретной машине; важен проверенный выходной архив и журнал задания.

STL не хранит единицы. Экспорт Blender переводит evaluated geometry и world transforms в миллиметры, затем независимый анализ проверяет габариты. Существующие объекты, выделение и несохраненные изменения сцены сохраняются. Автоматическое уменьшение и изменение функциональной геометрии не применяются.

## Подтверждение задания

Сохраненные состояния разделяют подготовленный файл, загрузку, отправку старта, принятие, подготовку принтера, печать и конечный результат. `request_id` идемпотентен. Намерение старта записывается до публикации команды. После неопределенного исхода допускается сверка по свежим данным, без слепого повтора.

`gcode_state=RUNNING` одного недостаточно: часть прошивок сообщает его во время подготовки. Нужны совпадение идентичности задания и признаки фактической печати, доступные этой модели. Камера дает наблюдение, но не удостоверяет свободный стол и не заменяет телеметрию задания.

## Лицензии и другие маршруты

Собственный код MCP лицензирован MIT. Bambu Studio лицензирован AGPL-3.0; его networking plugin содержит non-free библиотеки. Внешние программы устанавливаются отдельно. При распространении заимствованных компонентов нужно сохранить их лицензии и предоставить требуемые исходники. [Лицензия и описание Studio](https://github.com/bambulab/BambuStudio/blob/master/README.md), [LICENSE](https://github.com/bambulab/BambuStudio/blob/master/LICENSE).

[FULU OrcaSlicer-bambulab](https://github.com/FULU-Foundation/OrcaSlicer-bambulab) доступен как отдельный fork с восстановленным облачным маршрутом, но не является зависимостью продукта. Для A1 mini выбран путь, доступный через включаемый владельцем LAN Developer Mode.
