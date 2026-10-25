# Сторонние компоненты

Проверено 25.10.2026 по `pyproject.toml`, метаданным и файлам лицензий установленного окружения. Собственный код Bambu Workflow MCP распространяется по MIT, см. [LICENSE](../LICENSE).

## Прямые зависимости

| Компонент | Диапазон в проекте | Проверенная версия | Лицензия |
| --- | --- | --- | --- |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | `>=1.20,<2` | 1.30.0 | MIT |
| [paho-mqtt](https://github.com/eclipse-paho/paho.mqtt.python) | `>=2.1,<3` | 2.1.0 | EPL-2.0 или EDL-1.0 (BSD-3-Clause) |
| [trimesh](https://github.com/mikedh/trimesh) | `>=4.9,<5` | 4.12.2 | MIT |
| [NumPy](https://github.com/numpy/numpy) | `>=2,<3` | 2.5.4 | `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` в метаданных этой сборки |
| [keyring](https://github.com/jaraco/keyring) | `>=25,<26` | 25.7.0 | MIT |
| [filelock](https://github.com/tox-dev/filelock) | `>=3.16,<4` | 3.32.7 | MIT в установленной версии |

Инструменты разработки: [pytest](https://github.com/pytest-dev/pytest) `>=8,<10` (проверена 9.1.1, MIT), [build](https://github.com/pypa/build) `>=1,<2` (проверена 1.6.1, MIT). Сборочный backend [hatchling](https://github.com/pypa/hatch) `>=1.27,<2` использует MIT и устанавливается в изолированное окружение сборки.

Диапазоны допускают другие версии. При выпуске готового окружения нужно сверить лицензии фактически упакованных версий, сохранить их уведомления и лицензии транзитивных зависимостей. Бинарная сборка NumPy также содержит собственные уведомления сторонних компонентов; одной строки BSD недостаточно для их замены.

## Внешние программы и профили

- [Bambu Studio](https://github.com/bambulab/BambuStudio/blob/master/README.md): AGPL-3.0. Пользователь устанавливает его отдельно; MCP вызывает исполняемый файл через CLI.
- Закрытый Bambu networking plugin: в README Studio обозначен как optional и основанный на non-free библиотеках. Он не включен в этот проект и не нужен выбранному LAN адаптеру.
- Blender и его MCP: устанавливаются отдельно. Команда подключения задается пользователем. Их код, расширения и дистрибутивы в пакет не входят; условия зависят от выбранного MCP-пакета.
- FULU OrcaSlicer-bambulab, Bambu Connect и облачные учетные данные не являются зависимостями продукта.

Исходные профили слайсера не копируются в репозиторий или пакет. Они читаются из установки пользователя, разрешенные JSON сохраняются в локальном state конкретного задания вместе с проектом и manifest. Эти локальные артефакты не входят в дистрибутив MCP. Если их решено распространять отдельно, нужно учитывать лицензии исходных профилей и содержимого модели.

Первичные сведения: [paho LICENSE](https://github.com/eclipse-paho/paho.mqtt.python/blob/master/LICENSE.txt), [filelock LICENSE](https://github.com/tox-dev/filelock/blob/main/LICENSE), [Hatch LICENSE](https://github.com/pypa/hatch/blob/master/LICENSE.txt), [Bambu Studio LICENSE](https://github.com/bambulab/BambuStudio/blob/master/LICENSE).
