## Запуск:
```
sudo systemctl start resave
```

## Остановка:
```
sudo systemctl stop resave
```

## Перезапуск:
```
sudo systemctl restart resave
```

## Мягкая перезагрузка конфигурации:

```
sudo systemctl reload resave
```

## Статус:
```
systemctl status resave
```

---

# АВТОЗАПУСК

## Включить автозапуск при ребуте сервера:
```
sudo systemctl enable resave
```

## Выключить автозапуск:
```
sudo systemctl disable resave
```

## Проверить, включён ли:
```
systemctl is-enabled resave
```

---

# ЛОГИ

## Все логи сервиса:
```
journalctl -u resave
```

## Логи в реальном времени:
```
journalctl -u resave -f
```

## Логи только за сегодня:
```
journalctl -u resave --since today
```

## Последние 100 строк:
```
journalctl -u resave -n 100
```

---

# ЕСЛИ ПРАВИЛ СЕРВИС-ФАЙЛ

После любого изменения `/etc/systemd/system/resave.service`:

```
sudo systemctl daemon-reload
sudo systemctl restart resave
```

---

# УДАЛИТЬ СЕРВИС

```
sudo systemctl stop resave
sudo systemctl disable resave
sudo rm /etc/systemd/system/resave.service
sudo systemctl daemon-reload
```