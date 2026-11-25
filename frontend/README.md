# Статика интерфейса (CSS)

Раньше каждая страница грузила `cdn.tailwindcss.com` — компилятор Tailwind, который **в браузере** при каждом
открытии страницы и при каждом изменении DOM заново строил CSS, плюс шрифты и иконки с внешних CDN.
Теперь CSS собран заранее и лежит в `employees/static/app/` (закоммичен: Docker-образу Node не нужен).

| Файл | Для чего | Конфиг |
|---|---|---|
| `employees/static/app/app.css` | все страницы на `base.html` | `tailwind.app.config.js` |
| `employees/static/app/login.css` | `login.html` | `tailwind.login.config.js` |

Шрифты (IBM Plex Sans, Manrope), Font Awesome 6.4.0, IMask 7.6.0 и SheetJS 0.20.3 — копии тех же версий,
что раньше шли с CDN: `employees/static/vendor/`.

## Когда пересобирать

После изменения шаблонов, если появились **новые** классы Tailwind (которых ещё нет ни в одном шаблоне):

```sh
cd frontend
npm install        # один раз
npm run build      # пересобирает app.css и login.css
```

Версия Tailwind зафиксирована (3.4.17 — та же, что отдавал CDN). Классы ищутся в `employees/templates/**/*.html`,
включая JS внутри шаблонов, поэтому имя класса должно встречаться в коде целиком (`'bg-red-50'`, а не
`'bg-' + color + '-50'`).

Подключение в шаблоне: `{% load assets %}` и `{% asset 'app/app.css' %}` — к адресу добавляется `?v=<хэш содержимого>`,
поэтому nginx отдаёт `/static/` с кэшем на год, а после пересборки браузер сразу берёт новый файл.
