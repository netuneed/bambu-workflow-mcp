Тестовый STL: пластинка 12 x 12 x 1,2 мм. Размеры заданы в миллиметрах. Файл не содержит профиля принтера или готового G-code.

Подготовка без запуска:

```powershell
bambu-workflow prepare --printer workshop-mini --request-id acceptance-001 --file examples/acceptance-coupon-12x12x1.2mm.stl
```

Для реальной проверки stdio и восстановления после закрытия MCP:

```powershell
python scripts/smoke_mcp.py --file examples/acceptance-coupon-12x12x1.2mm.stl --request-id acceptance-mcp-001 --printer workshop-mini
```

Этот smoke вызывает только `prepare_print` и `job_status`. Для upload-only проверки уже нарезанного файла есть `scripts/hardware_upload_acceptance.py --file PATH_TO_SLICED_3MF --printer workshop-mini --report PATH_TO_NEW_REPORT_JSON`. Этот скрипт проверяет и передает файл, но не запускает его. Повтор с тем же отчетом возвращает прежний результат.

Физический тест запускается только по отдельному поручению владельца после подготовки стола.
