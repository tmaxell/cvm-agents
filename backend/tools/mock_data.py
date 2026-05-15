"""
Mock-данные AdTarget для демонстрации без VPN.

Активируется через env: ADTARGET_MOCK=true (или когда API недоступен).
Данные приближены к реальной структуре AdTarget.
"""

# ── Целевые группы ────────────────────────────────────────────────────────────

MOCK_TARGET_GROUPS = {
    "items": [
        {"id": 101, "name": "Спящие абоненты (90д+)", "clientsCount": 48320, "status": "Active"},
        {"id": 102, "name": "Низкий ARPU (<300₽)", "clientsCount": 112540, "status": "Active"},
        {"id": 103, "name": "Абоненты 18-29 лет", "clientsCount": 89700, "status": "Active"},
        {"id": 104, "name": "Корпоративные клиенты", "clientsCount": 14230, "status": "Active"},
        {"id": 105, "name": "Утилизаторы пакета данных (≥80%)", "clientsCount": 67890, "status": "Active"},
        {"id": 106, "name": "Абоненты без интернет-пакета", "clientsCount": 203410, "status": "Active"},
        {"id": 107, "name": "Высокий churn-риск", "clientsCount": 31200, "status": "Active"},
        {"id": 108, "name": "VIP-клиенты (ARPU>2000₽)", "clientsCount": 8750, "status": "Active"},
        {"id": 109, "name": "День рождения (±7 дней)", "clientsCount": 9240, "status": "Active"},
        {"id": 110, "name": "Новые абоненты (≤30 дней)", "clientsCount": 22100, "status": "Active"},
    ],
    "totalCount": 10,
    "page": 1,
    "pageSize": 20,
}

# ── Demo contact-base profile for prototype segmentation ─────────────────────

MOCK_CONTACT_BASE_PROFILE = {
    "metadata": {
        "mode": "demo_only",
        "source": "mock_data.py",
        "notes": (
            "Агрегированные признаки для прототипа сегментации; "
            "не являются prod-интеграцией и не подтверждают фактический размер сегмента."
        ),
    },
    "tariff": {
        "families": ["Smart", "Unlimited", "Family", "Archive"],
        "price_tiers": ["low", "mid", "premium"],
        "has_data_pack": True,
    },
    "arpu_bands": ["<300₽", "300-700₽", "700-1500₽", ">1500₽"],
    "data_usage": {
        "monthly_gb_bands": ["0-2", "2-8", "8-20", "20+"],
        "package_utilisation_bands": ["low", "medium", "high", "overage"],
        "night_usage": "available_as_demo_signal",
    },
    "churn_risk": {
        "bands": ["low", "medium", "high"],
        "signals": ["declining_usage", "support_complaints", "competitor_porting_interest"],
    },
    "roaming": {
        "international_roaming": ["none", "occasional", "frequent"],
        "domestic_travel": ["low", "medium", "high"],
    },
    "device_app_activity": {
        "device_os": ["iOS", "Android", "Other"],
        "smartphone_flag": True,
        "app_activity_bands": ["inactive", "occasional", "active"],
        "volte_capable": "demo_signal",
    },
    "consent_contactability_demo_metadata": {
        "sms_contactable": "requires_separate_validation",
        "push_contactable": "requires_separate_validation",
        "email_contactable": "requires_separate_validation",
        "opt_out": "requires_separate_validation",
        "frequency_cap": "requires_separate_validation",
    },
}

# ── Каналы коммуникаций ───────────────────────────────────────────────────────

MOCK_CHANNELS = [
    {"id": 1, "name": "SMS push", "contentType": "SmsContent", "isActive": True},
    {"id": 2, "name": "Flash SMS push", "contentType": "FlashSmsContent", "isActive": True},
    {"id": 3, "name": "USSD push", "contentType": "UssdContent", "isActive": True},
    {"id": 4, "name": "Email push", "contentType": "EmailContent", "isActive": True},
    {"id": 5, "name": "Text push (мобильный)", "contentType": "CustomContent", "isActive": True},
    {"id": 6, "name": "Json push", "contentType": "JsonContent", "isActive": True},
    {"id": 7, "name": "Text pull (USSD входящий)", "contentType": "TextPullContent", "isActive": True},
]

# ── События (триггеры) ────────────────────────────────────────────────────────

MOCK_EVENTS = [
    {
        "code": "DataPackageUtilization",
        "name": "Утилизация пакета данных",
        "parameters": [
            {"name": "UtilizationPercent", "type": "Decimal", "description": "Процент использования пакета (0-100)"},
        ],
    },
    {
        "code": "LowBalance",
        "name": "Низкий баланс",
        "parameters": [
            {"name": "BalanceAmount", "type": "Decimal", "description": "Сумма баланса"},
        ],
    },
    {
        "code": "ZeroBalance",
        "name": "Нулевой баланс",
        "parameters": [],
    },
    {
        "code": "Roaming",
        "name": "Вход в роуминг",
        "parameters": [
            {"name": "CountryCode", "type": "String", "description": "Код страны"},
        ],
    },
    {
        "code": "Birthday",
        "name": "День рождения абонента",
        "parameters": [
            {"name": "DaysOffset", "type": "Int", "description": "Смещение в днях от дня рождения"},
        ],
    },
    {
        "code": "FirstCall",
        "name": "Первый звонок после пополнения",
        "parameters": [],
    },
    {
        "code": "VoicePackageUtilization",
        "name": "Утилизация голосового пакета",
        "parameters": [
            {"name": "UtilizationPercent", "type": "Decimal", "description": "Процент использования"},
        ],
    },
    {
        "code": "ContractExpiry",
        "name": "Истечение контракта",
        "parameters": [
            {"name": "DaysToExpiry", "type": "Int", "description": "Дней до истечения"},
        ],
    },
]

# ── Шаблоны офферов (Business Transaction) ────────────────────────────────────

MOCK_OFFER_TEMPLATES = [
    {
        "id": 201,
        "name": "Пакет данных 5 ГБ",
        "businessOperation": {
            "id": "ActivateDataPackage5GB",
            "name": "Активация пакета 5 ГБ",
        },
        "parameters": [
            {"name": "ValidDays", "type": "Int", "defaultValue": 30},
            {"name": "Price", "type": "Decimal", "defaultValue": 150.0},
        ],
    },
    {
        "id": 202,
        "name": "Скидка 20% на тариф",
        "businessOperation": {
            "id": "ApplyTariffDiscount",
            "name": "Применить скидку на тариф",
        },
        "parameters": [
            {"name": "DiscountPercent", "type": "Int", "defaultValue": 20},
            {"name": "ValidDays", "type": "Int", "defaultValue": 30},
        ],
    },
    {
        "id": 203,
        "name": "Бонусные минуты 100",
        "businessOperation": {
            "id": "AddBonusMinutes",
            "name": "Начисление бонусных минут",
        },
        "parameters": [
            {"name": "Minutes", "type": "Int", "defaultValue": 100},
        ],
    },
    {
        "id": 204,
        "name": "Безлимитный интернет на 3 дня",
        "businessOperation": {
            "id": "ActivateUnlimitedInternet",
            "name": "Активация безлимитного интернета",
        },
        "parameters": [
            {"name": "ValidDays", "type": "Int", "defaultValue": 3},
        ],
    },
]

# ── Типы кампаний ─────────────────────────────────────────────────────────────

MOCK_CAMPAIGN_TYPES = [
    {"id": 1, "name": "Маркетинговая", "description": "Промо-акции и рекламные кампании"},
    {"id": 2, "name": "Сервисная", "description": "Сервисные уведомления"},
    {"id": 3, "name": "Транзакционная", "description": "Бизнес-транзакции и активации"},
    {"id": 4, "name": "Событийная", "description": "Кампании по событиям"},
]

# ── Группы кампаний ───────────────────────────────────────────────────────────

MOCK_CAMPAIGN_GROUPS = [
    {"id": 10, "name": "Удержание"},
    {"id": 11, "name": "Монетизация"},
    {"id": 12, "name": "Реактивация"},
    {"id": 13, "name": "Онбординг"},
    {"id": 14, "name": "Промо"},
]

# ── Результат создания кампании ───────────────────────────────────────────────

def make_mock_campaign_result() -> dict:
    """Возвращает реалистичный результат POST /Campaigns."""
    import random
    return {
        "campaignId": random.randint(10000, 99999),
        "errors": [],
        "warnings": [],
    }

# ── Результат валидации ───────────────────────────────────────────────────────

MOCK_VALIDATION_OK = {
    "errors": [],
    "warnings": [],
}

# ── Результаты runtime-действий ───────────────────────────────────────────────

def make_mock_start_result(campaign_id: int) -> list:
    return [{"id": campaign_id, "isSuccess": True, "errors": []}]


def make_mock_pause_result(campaign_id: int) -> list:
    return [{"id": campaign_id, "isSuccess": True, "errors": []}]
