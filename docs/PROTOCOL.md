# Выбранный путь и протокол

Исследование выполнено 25.10.2026. Основной маршрут: внешний установленный Bambu Studio CLI, затем LAN MQTT и FTPS. Исходный код слайсера и закрытый сетевой плагин не включаются в пакет. Облачная авторизация и Bambu Connect не используются как доказательство автоматического запуска.

## A1 mini и режим доступа

Официальная история A1 mini указывает `01.05.00.00` от 03.06.2025 как начало Authorization Control и Developer Mode. В обычном режиме неавторизованные команды могут отклоняться при доступной телеметрии. Developer Mode доступен в LAN Mode и отключает проверку команд. Наличие связи, статуса или заявленного флага режима не заменяет аппаратную проверку управления.

Источники: [история прошивок A1 mini](https://wiki.bambulab.com/en/a1-mini/manual/a1-mini-firmware-release-history), [включение Developer Mode](https://wiki.bambulab.com/en/knowledge-sharing/enable-developer-mode), [Bambu blog от 20.01.2025](https://blog.bambulab.com/updates-and-third-party-integration-with-bambu-connect/), [Security White Paper 2025.09](https://cdn1.bambulab.com/trust-center/file/bambulab-security-whitepaper-en.pdf).

Обе wiki были прочитаны прямым HTTPS-запросом после ошибки извлечения через web-инструмент. Страница истории прошивок имела дату обновления 03.06.2026, инструкция режима: 15.10.2025.

## LAN

MQTT использует TLS на 8883, пользователя `bblp`, LAN access code и темы `device/<serial>/request`, `device/<serial>/report`. Свежая полная сводка запрашивается `pushing.command="pushall"`. Pause/resume/stop имеют `print.command` со значениями `pause`, `resume`, `stop`, пустой `param` и отдельный `sequence_id`.

Эти команды видны в [DeviceManager.cpp](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/DeviceManager.cpp). Формат следует сверять с установленной прошивкой. Запись команды в сокет подтверждает передачу, но не выполнение.

FTPS использует implicit TLS на 990 и защищенный data channel. Для прошивок, требующих TLS session reuse, data connection должна использовать сессию control connection. Подготовленный файл получает уникальное имя задания. Контрольная сумма локального снимка сравнивается до отправки и после получения файла с принтера; одна проверка размера недостаточна.

Запуск выбранной пластины обычно использует `print.command="project_file"`, `param="Metadata/plate_1.gcode"`, URL файла на SD и md5 выбранного вложенного G-code. Это протокол сторонних реализаций, а не публичный контракт Bambu. В открытом Studio эти детали переданы закрытому networking plugin. Первичный источник сторонней реализации: [voska/bambu protocol](https://github.com/voska/bambu/blob/main/docs/protocol.md). Открытая граница Studio: [PrintJob.cpp](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/Jobs/PrintJob.cpp), [bambu_networking.hpp](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/Utils/bambu_networking.hpp).

Основной workflow загружает sliced архив под уникальным именем `.3mf`, чтобы использовать `project_file` с явной пластиной и mapping. Расширение локального `prepared.gcode.3mf` не определяет имя на SD. Низкоуровневый адаптер отдельно поддерживает legacy `gcode_file` для имени `.gcode.3mf`, но только для одной пластины с внешней подачей, без project filament selectors. Эта ветка не подтверждена аппаратным запуском новой MCP и не подменяет проверку основного маршрута.

В A1 mini локальная камера использует порт 6000. RTSP других серий не переносится на A1 mini. Upstream: [MediaPlayCtrl.cpp](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/MediaPlayCtrl.cpp), [N1.json](https://github.com/bambulab/BambuStudio/blob/master/resources/printers/N1.json).

## Слайсинг

Официальная [CLI wiki](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage), обновленная 31.03.2026, определяет `--slice 0` как все пластины, `--slice i` как номер пластины i. Рабочая MCP должна проверять фактический `--help` установленной версии.

`--load-settings` и `--load-filaments` требуют полных конфигураций. Ресурсный JSON может содержать только отличия от родителя. В профиле A1 mini также есть `include` для G-code шаблонов. Разрешать только `inherits` недостаточно. [Профиль A1 mini 0.4](https://github.com/bambulab/BambuStudio/blob/master/resources/profiles/BBL/machine/Bambu%20Lab%20A1%20mini%200.4%20nozzle.json).

STL не хранит единицы. Экспорт Blender переводит evaluated geometry и world transforms в миллиметры, затем независимый анализ проверяет габариты. Существующие объекты, выделение и несохраненные изменения сцены сохраняются. Автоматическое уменьшение и изменение функциональной геометрии не применяются.

## Подтверждение задания

Сохраненные состояния разделяют подготовленный файл, загрузку, отправку старта, принятие, подготовку принтера, печать и конечный результат. `request_id` идемпотентен. Намерение старта записывается до публикации команды. После неопределенного исхода допускается сверка по свежим данным, без слепого повтора.

`gcode_state=RUNNING` одного недостаточно: часть прошивок сообщает его во время подготовки. Нужны совпадение идентичности задания и признаки фактической печати, доступные этой модели. Камера дает наблюдение, но не удостоверяет свободный стол и не заменяет телеметрию задания.

## Лицензии и другие маршруты

Собственный код MCP лицензирован MIT. Bambu Studio лицензирован AGPL-3.0; его networking plugin содержит non-free библиотеки. Внешние программы устанавливаются отдельно. При распространении заимствованных компонентов нужно сохранить их лицензии и предоставить требуемые исходники. [Лицензия и описание Studio](https://github.com/bambulab/BambuStudio/blob/master/README.md), [LICENSE](https://github.com/bambulab/BambuStudio/blob/master/LICENSE).

[FULU OrcaSlicer-bambulab](https://github.com/FULU-Foundation/OrcaSlicer-bambulab) доступен как отдельный fork с восстановленным облачным маршрутом, но не является зависимостью продукта. Для A1 mini выбран путь, доступный через включаемый владельцем LAN Developer Mode.
