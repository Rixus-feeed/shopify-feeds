# Google Merchant фіди для Shopify-магазинів

Скрипт щогодини забирає всі активні товари з кожного магазину через Shopify API і публікує по одному XML-файлу на магазин за постійним посиланням:

```
https://<ваш-github-логін>.github.io/<назва-репозиторію>/rixus.xml
https://<ваш-github-логін>.github.io/<назва-репозиторію>/keratinequeen.xml
...
```

Ліміту 250 товарів немає. Один магазин = одне посилання, яке й дається боту.

## Крок 1. Доступ до API (для кожного магазину)

1. В адмінці магазину: аватар профілю → **Dev Dashboard** (або dev.shopify.com/dashboard).
2. **Create app** → назва, наприклад `Product feed`.
3. **Versions** → **Access scopes**: `read_products`, `read_inventory` → **Release**.
4. Вкладка **Home** → **Install** на потрібний магазин.
5. **Settings** → скопіювати **Client ID** і **Client secret**.

Токен скрипт отримує сам при кожному запуску (токени Dev Dashboard діють 24 год).

## Крок 2. Репозиторій на GitHub

1. Створити репозиторій (наприклад `shopify-feeds`) і завантажити туди всі файли з цієї папки, разом із `.github/workflows/feeds.yml`.
2. **Settings → Secrets and variables → Actions → New repository secret**:
   - Name: `STORES_JSON`
   - Value:
     ```json
     [
       {"slug": "rixus", "shop": "pxnqdi-92.myshopify.com", "client_id": "...", "client_secret": "..."},
       {"slug": "keratinequeen", "shop": "XXXX.myshopify.com", "client_id": "...", "client_secret": "..."},
       {"slug": "oliere", "shop": "XXXX.myshopify.com", "client_id": "...", "client_secret": "..."},
       {"slug": "sobeauty", "shop": "XXXX.myshopify.com", "client_id": "...", "client_secret": "..."},
       {"slug": "sinergy", "shop": "XXXX.myshopify.com", "client_id": "...", "client_secret": "..."}
     ]
     ```
     `shop` — технічна адреса `*.myshopify.com` (Settings → Domains в адмінці). `slug` — назва файлу фіду.
3. **Actions** → `Product feeds` → **Run workflow** (перший запуск вручну).
4. Після першого запуску: **Settings → Pages** → Source: **Deploy from a branch** → гілка `gh-pages`, папка `/ (root)` → Save.

За 1–2 хвилини фіди відкриються за посиланнями вище.

## Що в фіді

Формат Google Merchant (RSS 2.0, `g:`): id (SKU), title, description, link, image_link + до 10 additional_image_link, availability, quantity (залишок), price / sale_price, condition, brand, gtin (зі штрихкоду), mpn, identifier_exists, google_product_category, product_type, item_group_id, size (обʼєм), unit_pricing_measure, shipping_weight, product_detail (колекції й теги — для пошуку ботом), custom_label_0–2.

Обʼєм береться з опції «Об'єм» варіанту, а якщо там «Default Title» — з назви товару («250 мл», «10x6 мл», «50 г»).

У фід потрапляють активні товари, опубліковані в Online Store.

## Корисно знати

- **Частота:** щогодини (GitHub може запускати із затримкою 5–30 хв). Змінюється в `feeds.yml`, рядок `cron`.
- **Помилка одного магазину** не ламає інші; його попередній фід лишається.
- **60 днів без змін у репозиторії** — GitHub може призупинити розклад і надішле лист; запуск відновлюється однією кнопкою в Actions.
- Репозиторій може бути публічним: ключі зберігаються в Secrets і не видні. Самі фіди публічні (як і товари на сайті).
