# AdTarget — UI и пользовательские сценарии

> Источник: анализ дизайн-макетов и скриншотов (май 2026)

---

## Campaign Flow Editor

Визуальный редактор flow кампании — центральный экран платформы.

### Структура экрана

```
┌─────────────────┬──────────────────────────────┬────────────────────────┐
│  Левая панель   │       Canvas (flow)           │   Правая панель        │
│  (палитра       │                               │   (свойства выбранной  │
│   активностей)  │  [Common] → [Target group]    │    активности)         │
│                 │              → [SMS push] → … │                        │
└─────────────────┴──────────────────────────────┴────────────────────────┘
```

### Левая панель — типы активностей

**Communication (Push)**
- SMS push
- Flash SMS push
- USSD push
- USSD menu push
- Email push
- Text push
- Json push

**Communication (Pull)**
- Text pull
- Json pull
- USSD pull
- USSD menu pull
- USSD with header pull

**Custom communication**
- Кастомные типы коммуникаций

**Product action** → `BusinessTransactionActivity`

**Response** → `ResponseActivity`

**Business transaction** → `BusinessTransactionActivity`

### Свойства CommonActivity (правая панель)

| Поле | Тип | Обязательно |
|------|-----|-------------|
| Name | string | ✅ |
| Tags | tag[] | ❌ |
| Description | string | ❌ |
| Priority | 1-5 звёзд | ✅ (default=1) |
| Group | CampaignGroup | ❌ |
| Type | CampaignType | ❌ |
| Total ad limit | int | ❌ |
| Business transaction limit | int | ❌ |
| Considers contact policies | bool | ✅ |
| Blacklist | bool | ✅ |
| Has impact on contact policies | bool | ✅ |
| Timezone | System \| Client | ✅ |

### Типичные ошибки валидации в UI

Красный кружок на ноде = ошибка валидации.

| faultCode | Нода | Решение |
|-----------|------|---------|
| TargetGroupNotSet | Target group | Выбрать ЦГ в правой панели |
| InvalidSchedule | Common | Исправить даты расписания |
| BranchWithControlActivitiesOnly | любая | Добавить активное действие в ветку |
| TestGroupNotFound | Target group | Указать корректную тестовую группу |

---

## Сценарии использования

### Сценарий 1: Создание SMS-кампании вручную
1. Нажать «Новая кампания»
2. В canvas появляется CommonActivity
3. Перетащить «Target group» из палитры
4. Выбрать ЦГ в правой панели
5. Перетащить «SMS push»
6. Написать текст в правой панели
7. Нажать «Валидировать» → исправить ошибки
8. Нажать «Сохранить» → «Запустить»

### Сценарий 2: Event-triggered кампания
1. Добавить Target group
2. Добавить Event (из группы Events в палитре)
3. Выбрать событие (например DataPackageUtilization)
4. Настроить фильтры события
5. Добавить SMS push / Business transaction
6. Запустить

### Боли пользователей (из исследований)
- Сложно понять почему кампания не проходит валидацию (непонятные faultCode)
- Долго искать нужную ЦГ в большом списке
- Забывают выставить корректные даты расписания
- Не знают какой шаблон оффера использовать для конкретной акции
- Трудно собрать правильную цепочку активностей для сложных сценариев

---

## Навигация платформы

Главное меню: Segmentation | Campaigns | Reporting | Approval | Templates | System | Configuration

| Раздел | URL | Что делают |
|--------|-----|-----------|
| Campaigns | /campaigns | Список кампаний |
| Campaign flow | /campaigns/{id}/flow | Редактор flow |
| Segmentation | /segmentation | Список ЦГ |
| Templates | /templates | Шаблоны кампаний |
| Reporting | /reporting | Отчётность |
