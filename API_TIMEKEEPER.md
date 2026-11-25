# Табелчи API — ходим маълумотини бошқа тизимдан янгилаш

Bu hujjat **tashqi tizim** dasturchilari uchun: табелчи o'z цехining xodimlarini boshqa tizimdan turib ko'rishi va ularning
ma'lumotini yangilashi uchun `employee_service` (ES) taqdim etadigan API. Hujjatdagi hamma narsa koddagi holatga mos
(testlar buni tekshiradi), shuning uchun faqat shunga tayanib integratsiya qilsa bo'ladi.

- Bazaviy manzil: `https://192.168.101.6:5000/api/v1/timekeeper/`
- Interaktiv hujjat (Swagger): `https://192.168.101.6:5000/api/docs/` — `timekeeper` bo'limi
- Ma'lumot formati: JSON (`Content-Type: application/json`). PATCH uchun `multipart/form-data` ham qabul qilinadi.
- Vaqt: ISO 8601, Toshkent vaqti (`+05:00`). Sanalar (tug'ilgan kun va h.k.) **`DD/MM/YYYY`** ko'rinishida qaytadi.
- Xato matnlari o'zbek (kirill) yoki ingliz tilida bo'lishi mumkin va o'zgarishi mumkin. Umumiy xatolarda (`detail`) mashina uchun
  barqaror `code` bor — **matnga emas, `code`ga tayaning**. Maydon xatolarida (`{"maydon": ["…"]}`) maydon nomiga tayaning.

## 1. Qisqacha: qanday ishlaydi

```
1. POST /token/           login + parol          ->  token (24 soat amal qiladi)
2. GET  /employees/       Authorization: Token…  ->  o'z цехidagi xodimlar
3. GET  /employees/{tabel_number}/                ->  bitta xodim
4. PATCH /employees/{tabel_number}/  {maydonlar}  ->  yangilangan xodim
5. POST /logout/                                  ->  tokenni bekor qilish (ixtiyoriy)
```

Asosiy qoidalar:

- Token **табелчining o'z hisobi** (ES'dagi login va parol) bilan olinadi. Administrator, HR va oddiy xodimlarga token **berilmaydi**.
- Табелчи faqat **o'z цехining** xodimlarini ko'radi va o'zgartiradi. Boshqa цех xodimi — `404` (mavjudligi ham bildirilmaydi).
- Har bir so'rovda server табелчining huquqini qayta tekshiradi: табелчи «Табелчилар» sahifasida olib tashlansa yoki
  hisobi/xodimi faol bo'lmay qolsa, token darhol ishlamay qoladi (`403`, `not_a_timekeeper`).
- ES **xizmat kaliti** (`X-Employee-Service-Key`) va veb-sessiya bu API'da **ishlamaydi** — faqat табелчи tokeni.
- Qoidalar veb-sahifadagi «Цех ходимлари» bilan bir xil: hisob (login, parol, rol) va цех o'zgarmaydi, administrator/HR
  xodimiga tegib bo'lmaydi.

## 2. Autentifikatsiya

### 2.1 Token olish — `POST /api/v1/timekeeper/token/`

Autentifikatsiya sarlavhasi **kerak emas**.

```http
POST /api/v1/timekeeper/token/
Content-Type: application/json

{"login": "ali_login", "password": "…"}
```

`200 OK`:

```json
{
  "token": "tk_zBCHsRvnSN5LHKqb1LI-rrY4cios-Jyw5IvJyy3BajE",
  "token_type": "Token",
  "expires_at": "2026-11-26T15:35:47.570655+05:00",
  "expires_in": 86399,
  "timekeeper": {
    "employee_id": 1,
    "tabel_number": "101",
    "full_name": "Валиев Али",
    "login": "ali_login",
    "department": {"id": 1, "name": "1-Цех Биринчи"}
  }
}
```

- `token` faqat shu javobda ko'rinadi, keyin uni qayta olib bo'lmaydi (bazada faqat xeshi saqlanadi).
- Muddat: **24 soat** (serverda `TIMEKEEPER_API_TOKEN_TTL_HOURS`). Bir foydalanuvchida bir vaqtda **5 tagacha** faol token
  bo'ladi (`TIMEKEEPER_API_MAX_TOKENS`); 6-si olinganda eng eskisi o'chadi.

Xatolar:

| HTTP | `code` | Sabab |
|---|---|---|
| 400 | — | `login` yoki `password` yuborilmagan (`{"password": ["…"]}`) |
| 401 | `invalid_credentials` | Login yoki parol noto'g'ri (yoki hisob o'chirilgan). Login yo'qmi yoki parol xatomi — javob bir xil |
| 403 | `not_a_timekeeper` | Parol to'g'ri, lekin bu ishlab turgan табелчи emas (administrator, HR, oddiy xodim yoki табелчilikdan olib tashlangan) |
| 429 | `login_locked` | Juda ko'p noto'g'ri urinish: 5 ta xato (login + IP) → 15 daqiqa. `Retry-After` sarlavhasi bor |
| 429 | `throttled` | Bir IP'dan daqiqasiga 10 tadan ko'p token so'rovi |

> Noto'g'ri parol urinishlari **veb-kirish bilan umumiy hisoblanadi**: API orqali 5 marta xato qilingan login veb-sahifadan ham
> 15 daqiqaga bloklanadi.

### 2.2 Tokenni ishlatish

Har bir so'rovga qo'shing:

```http
Authorization: Token tk_zBCHsRvnSN5LHKqb1LI-rrY4cios-Jyw5IvJyy3BajE
```

Token boshqa ES API'larida (`/api/v1/employees/` va h.k.) **ishlamaydi**.

Token yaroqsiz bo'lsa javob `401` va `WWW-Authenticate: Token` sarlavhasi, tanasida `code`:

| `code` | Ma'nosi | Nima qilish kerak |
|---|---|---|
| `not_authenticated` | `Authorization` sarlavhasi yo'q | Sarlavhani qo'shing |
| `bad_header` | Sarlavha `Token <token>` ko'rinishida emas | Formatni to'g'rilang |
| `token_invalid` | Token noto'g'ri yoki bekor qilingan | Qayta login qiling |
| `token_expired` | Muddati tugagan | Qayta login qiling |
| `password_changed` | Табелчи parolini almashtirgan | Qayta login qiling (yangi parol bilan) |
| `account_disabled` | Hisob o'chirilgan | Administratorga murojaat qiling |

`403` + `not_a_timekeeper` — token yaroqli, lekin табелчи endi ishlab turgan табелчи emas (olib tashlangan yoki xodimi faol emas).
Qayta login qilish yordam bermaydi: administrator «Табелчилар» sahifasida qayta tayinlashi kerak.

### 2.3 Tokenni bekor qilish — `POST /api/v1/timekeeper/logout/`

Joriy tokenni o'chiradi. `204 No Content`, tanasi yo'q. Ishdan keyin chaqirish tavsiya etiladi.

### 2.4 Kim ekanligini bilish — `GET /api/v1/timekeeper/me/`

```json
{
  "timekeeper": {
    "employee_id": 1, "tabel_number": "101", "full_name": "Валиев Али", "login": "ali_login",
    "department": {"id": 1, "name": "1-Цех Биринчи"}
  },
  "token": {"created_at": "2026-11-25T15:35:47+05:00", "expires_at": "2026-11-26T15:35:47+05:00"}
}
```

## 3. Ma'lumotnomalar — `GET /api/v1/timekeeper/reference/`

Yangilashda kerak bo'ladigan `id` va qiymatlar. Formani to'ldirish uchun shu javobdan foydalaning.

```json
{
  "department": {"id": 1, "name": "1-Цех Биринчи"},
  "sections": [
    {"id": 1, "code": "1", "name": "Юридик бўлим", "path": "Юридик бўлим", "parent_id": null},
    {"id": 2, "code": "1", "name": "Ички участка", "path": "Юридик бўлим / Ички участка", "parent_id": 1}
  ],
  "position_categories": [{"id": 1, "name": "Раҳбар"}, {"id": 2, "name": "Мутахассис"}],
  "employee_categories": [{"id": 1, "name": "Бошқарув ходимлари"}],
  "work_schedules": [{"id": 1, "name": "5 кунлик иш ҳафтасида"}, {"id": 2, "name": "Сменада"}],
  "statuses": [
    {"value": "working", "label": "Фаол"},
    {"value": "vacation", "label": "Меҳнат таътилида"},
    {"value": "business_trip", "label": "Хизмат сафарида"},
    {"value": "sick_leave", "label": "Касаллик таътилида"},
    {"value": "childcare_leave", "label": "Бола парваришлаш таътилида"},
    {"value": "over_staff", "label": "св.шт."},
    {"value": "inactive", "label": "Фаол эмас"}
  ],
  "genders": [{"value": "M", "label": "Эркак"}, {"value": "F", "label": "Аёл"}]
}
```

`sections` — **faqat o'z цехining** бўлимлари (ichma-ich бўлим ota-бўлимidan keyin keladi). Toifalar va ish tartiblari umumiy ro'yxat.

## 4. Xodimlar ro'yxati — `GET /api/v1/timekeeper/employees/`

Faqat o'z цехidagi, o'chirilmagan xodimlar. Tartib «Штат бирлиги ва лавозим» sahifasidagi kabi: бўлимлар
«ID бўлим» bo'yicha, ichida лавозим tartibi, keyin familiya.

| Parametr | Ma'nosi |
|---|---|
| `search` | Ism, familiya, otasining ismi, табель yoki лавозимdan qidiradi. Bir nechta so'z — hammasi mos kelishi kerak |
| `tabel_number` | Aniq табель raqami |
| `status` | Bitta holat: `working`, `vacation`, `business_trip`, `sick_leave`, `childcare_leave`, `over_staff`, `inactive` |
| `updated_since` | ISO 8601 vaqt: shundan keyin o'zgargan xodimlar (sinxronlash uchun), masalan `2026-11-25T09:00:00+05:00` |
| `page`, `page_size` | Sahifalash. `page_size` 1–200, sukut bo'yicha 50 |

```json
{
  "count": 2,
  "next": "https://192.168.101.6:5000/api/v1/timekeeper/employees/?page=2&page_size=1",
  "previous": null,
  "results": [ { "…": "xodim (6-bo'limga qarang)" } ]
}
```

Noto'g'ri `status` yoki `updated_since` — `400` (`{"status": ["…"]}`).

## 5. Bitta xodim — `GET /api/v1/timekeeper/employees/{tabel_number}/`

`{tabel_number}` — xodimning табель raqami (matn, masalan `102`; URL-xavfsiz bo'lishi kerak, `/` bo'lmasligi lozim).
Boshqa цех, o'chirilgan yoki mavjud bo'lmagan xodim uchun javob bir xil: `404`, `{"detail": "…", "code": "not_found"}`.

## 6. Xodim obyekti (o'qish)

```json
{
  "id": 2,
  "tabel_number": "102",
  "slug": "9154-bobur",
  "first_name": "Бобур",
  "last_name": "Тошев",
  "surname": "Алишерович",
  "full_name": "Тошев Бобур Алишерович",
  "gender": "M",
  "date_of_birth": "30/12/1990",
  "phone_number_1": "+998 90 111 22 33",
  "phone_number_2": "",
  "department": {"id": 1, "name": "1-Цех Биринчи"},
  "section": {"id": 1, "code": "1", "name": "Юридик бўлим", "path": "Юридик бўлим"},
  "position": "Бош муҳандис",
  "grade": "4*",
  "date_of_employment": "01/05/2020",
  "date_of_change_position": "15/01/2024",
  "position_category": {"id": 2, "name": "Мутахассис"},
  "employee_category": null,
  "work_schedule": {"id": 1, "name": "5 кунлик иш ҳафтасида"},
  "status": "vacation",
  "status_label": "Меҳнат таътилида",
  "is_active": true,
  "work_status": "vacation",
  "replacement": {"id": 1, "tabel_number": "101", "full_name": "Валиев Али"},
  "special_clothing_size": "", "clothe_size": "", "shoe_size": "42", "jacket_size": "", "tshirt_size": "",
  "base_image_url": "https://192.168.101.6:5000/media/employee_base_images/9154-bobur.jpg",
  "base_image_thumb_url": "https://192.168.101.6:5000/media/thumbs/256v2/employee_base_images/9154-bobur.jpg.jpg",
  "created_at": "2026-11-25T15:35:46+05:00",
  "updated_at": "2026-11-25T15:35:47+05:00",
  "is_protected": false
}
```

- `is_protected: true` — administrator yoki HR xodimi. Uni **o'zgartirib bo'lmaydi** (PATCH `403`, `permission_denied`).
- `status` — xodimning joriy holati (asosiy maydon). `is_active` va `work_status` — shu holatdan kelib chiqadigan qiymatlar, faqat o'qish uchun.
- `replacement` — ushbu xodimning o'rniga ishlayotgan xodim (`null` — yo'q).
- Sana maydonlari qaytganda `DD/MM/YYYY`, bo'lmasa `null`.
- Javobda **parol, login, rol, ichki integratsiya maydonlari yo'q**.

## 7. Xodimni yangilash — `PATCH /api/v1/timekeeper/employees/{tabel_number}/`

Faqat **yuborilgan** maydonlar o'zgaradi. Muvaffaqiyatda `200` va yangilangan xodim obyekti (6-bo'lim) qaytadi.
Amal **butunlay bajariladi yoki butunlay rad etiladi**: xato bo'lsa hech narsa saqlanmaydi.
`PUT` va `DELETE` yo'q (`405`).

```http
PATCH /api/v1/timekeeper/employees/102/
Authorization: Token tk_…
Content-Type: application/json

{"position": "Бош муҳандис", "status": "vacation", "replacement_tabel_number": "101"}
```

### 7.1 O'zgartirish mumkin bo'lgan maydonlar

| Maydon | Tur / format | Qoida |
|---|---|---|
| `first_name` | matn | Bo'sh bo'lmasin |
| `last_name` | matn | Bo'sh bo'lmasin |
| `surname` | matn | Bo'sh bo'lishi mumkin |
| `gender` | `"M"`, `"F"` yoki `""` | Ma'lumotnoma: `genders` |
| `date_of_birth` | sana yoki `null` | `DD.MM.YYYY`, `DD/MM/YYYY` yoki `YYYY-MM-DD`; `null` — tozalash |
| `phone_number_1`, `phone_number_2` | matn, ko'pi bilan 20 belgi | Tavsiya: `+998 90 123 45 67`; `""` — tozalash |
| `section_id` | son yoki `null` | `reference/` dagi `sections[].id`, **faqat o'z цехidan**; `null` — бўлимsiz |
| `position` | matn | Лавозим nomi. Штат qatori bilan nom bo'yicha moslanadi (bo'shliq va harf registri hisobga olinmaydi) |
| `grade` | `"4"`, `"12"`, `"12*"` | 1–2 xonali raqam, oxirida ixtiyoriy `*`. `"04*"` → `"4*"`. `""`/`null` — tozalash |
| `date_of_employment` | sana yoki `null` | Sana formatlari yuqoridagidek |
| `date_of_change_position` | sana yoki `null` | Sana formatlari yuqoridagidek |
| `position_category_id` | son yoki `null` | `reference/` → `position_categories[].id` |
| `employee_category_id` | son yoki `null` | `reference/` → `employee_categories[].id` |
| `work_schedule_id` | son yoki `null` | `reference/` → `work_schedules[].id` |
| `status` | matn | `working`, `vacation`, `business_trip`, `sick_leave`, `childcare_leave`, `over_staff`, `inactive` (7.2) |
| `replacement_tabel_number` | matn yoki `null` | O'rinbosarning табель raqami (7.2); `null` — o'rinbosarni olib tashlash |
| `special_clothing_size`, `clothe_size`, `shoe_size`, `jacket_size`, `tshirt_size` | matn | O'lchamlar, erkin matn |

### 7.2 Holat (`status`) va o'rinbosar

`status` veb-formadagi «Ҳолати» bilan bir xil:

| `status` | Ma'nosi | `is_active` |
|---|---|---|
| `working` | Фаол (ishda) | `true` |
| `vacation` | Меҳнат таътилида | `true` |
| `business_trip` | Хизмат сафарида | `true` |
| `sick_leave` | Касаллик таътилида | `true` |
| `childcare_leave` | Бола парваришлаш таътилида | `true` |
| `over_staff` | св.шт. (штатдан ташқари) | `true` |
| `inactive` | Фаол эмас (ishdan chiqqan) | `false` — xodimning ES hisobi ham o'chiriladi |

O'rinbosar (`replacement_tabel_number`) — xodim vaqtincha ishda bo'lmaganda uning o'rniga ishlaydigan xodim:

- `working` holatida o'rinbosar bo'lmaydi: `working`ga qaytarilsa mavjud o'rinbosar yozuvi avtomatik o'chadi.
- O'rinbosar **shu цехdan** bo'lishi, **faol** bo'lishi, o'zi ishda (`working` yoki `over_staff`) bo'lishi va xodimning o'zi bo'lmasligi shart.
  Aks holda `400` va `{"replacement_tabel_number": ["…"]}`.
- Bitta xodim bir nechta xodimning o'rniga ishlashi mumkin, lekin xodimga **bittadan ortiq** o'rinbosar qo'yib bo'lmaydi (yangisi eskisini almashtiradi).
- Holatni o'zgartirmasdan faqat o'rinbosarni olib tashlash: `{"replacement_tabel_number": null}`.

### 7.3 O'zgartirib bo'lmaydigan maydonlar

Bularni yuborsangiz `400` qaytadi va **hech narsa saqlanmaydi** (javobda har bir maydon uchun sabab bor):

| Maydon | Sabab |
|---|---|
| `tabel_number` | Xodimning kaliti, o'zgarmaydi |
| `department_id`, `department` | Xodim o'z цехида qoladi |
| `is_active`, `work_status` | `status` maydonidan foydalaning |
| `replacement_employee_id` | `replacement_tabel_number` dan foydalaning |
| `base_image` | Fotosurat — Face ID etaloni. Faqat veb-interfeysdan o'zgaradi |
| `login`, `password`, `password_confirm`, `role` | Hisob bu API orqali o'zgarmaydi |
| boshqa har qanday nomaʼlum maydon (`slug`, `metadata`, `source_system`, …) | Umumiy xabar: «Номаълум ёки ўзгартириш мумкин бўлмаган майдон» |

### 7.4 Tekshiruv tartibi va xato formati

1. Avval **maydon nomlari** tekshiriladi (7.3): barcha rad etilgan nomlar bir javobda qaytadi.
2. So'ng har bir **maydon qiymati** tekshiriladi: barcha xato maydonlar bir javobda qaytadi.
3. Maydonlar to'g'ri bo'lgandagina **maydonlararo** qoidalar tekshiriladi (masalan, `section_id` shu цехга tegishlimi).
   Shuning uchun xatolarni tuzatgach, yangi xato chiqishi mumkin.

Maydon xatosi (`400`) — `{maydon: [xabarlar]}` ko'rinishida (`code` **yo'q**):

```json
{
  "tabel_number": ["Табель рақами ўзгартирилмайди (у ходимнинг калити)."],
  "login": ["Ҳисоб (логин, парол, рол) бу API орқали ўзгармайди."]
}
```

```json
{
  "date_of_birth": ["Date has wrong format. Use one of these formats instead: DD.MM.YYYY, DD/MM/YYYY, YYYY-MM-DD."],
  "grade": ["Даража 1–2 хонали рақам бўлиши керак, охирида «*» қўйиш мумкин (масалан: 12 ёки 12*)."]
}
```

```json
{"section_id": ["Бўлим танланган цехга тегишли эмас."]}
```

```json
{"replacement_tabel_number": ["Ўринбосар ҳозир ишда бўлган фаол ходим бўлиши керак."]}
```

Umumiy xatolar (`detail` + `code`) — 8-bo'lim.

## 8. Xatolar: HTTP holatlari va `code`

Maydon xatolaridan (7.4) boshqa hamma xato `{"detail": "…", "code": "…"}` shaklida:

| HTTP | `code` | Qachon |
|---|---|---|
| 400 | `empty_update` | PATCH tanasi bo'sh (`{}`) |
| 400 | `parse_error` | Tana JSON obyekt emas (masalan, massiv yoki buzuq JSON) |
| 401 | `invalid_credentials`, `not_authenticated`, `bad_header`, `token_invalid`, `token_expired`, `password_changed`, `account_disabled` | 2-bo'limdagi jadvallarga qarang |
| 403 | `not_a_timekeeper` | Ishlab turgan табелчи emas (token olishda ham, ishlatishda ham) |
| 403 | `permission_denied` | Administrator/HR xodimini o'zgartirishga urinish (`is_protected: true`) |
| 404 | `not_found` | Xodim yo'q, o'chirilgan yoki boshqa цехда; yoki noto'g'ri `page` |
| 405 | `method_not_allowed` | Ruxsat etilmagan HTTP metod (`PUT`, `DELETE`, `POST /employees/`) |
| 415 | `unsupported_media_type` | Qo'llab-quvvatlanmaydigan `Content-Type` |
| 429 | `login_locked` | Token olishda parol urinishlari tugagan |
| 429 | `throttled` | So'rovlar chegarasi oshgan; `Retry-After` sarlavhasi |
| 5xx | — | Server xatosi. Biroz kutib qayta urinib ko'ring; takrorlansa xabar bering |

So'rovlar chegarasi: token olish — **10 ta/daqiqa** (IP bo'yicha), qolgan API — **120 ta/daqiqa** (foydalanuvchi bo'yicha).
`429` kelsa `Retry-After` soniyasicha kuting.

## 9. Kod namunalari

### 9.1 curl

```bash
BASE=https://192.168.101.6:5000/api/v1/timekeeper

# 1) token
TOKEN=$(curl -sk -X POST $BASE/token/ -H 'Content-Type: application/json' \
        -d '{"login":"ali_login","password":"…"}' | python -c 'import sys,json; print(json.load(sys.stdin)["token"])')

# 2) o'z цехidagi xodimlar
curl -sk "$BASE/employees/?search=Бобур" -H "Authorization: Token $TOKEN"

# 3) xodimni yangilash
curl -sk -X PATCH $BASE/employees/102/ -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' \
     -d '{"position":"Бош муҳандис","phone_number_1":"+998 90 555 44 33"}'

# 4) chiqish
curl -sk -X POST $BASE/logout/ -H "Authorization: Token $TOKEN"
```

(`-k` — faqat ichki tarmoqdagi o'z-o'zidan imzolangan sertifikat uchun, 10-bo'limga qarang.)

### 9.2 Python (`requests`) — token keshlash va avtomatik qayta login bilan

```python
import time
import requests

BASE = "https://192.168.101.6:5000/api/v1/timekeeper"
RELOGIN_CODES = {"token_invalid", "token_expired", "password_changed", "not_authenticated"}


class TimekeeperClient:
    def __init__(self, login, password, verify=True, timeout=30):
        self.login, self.password, self.verify, self.timeout = login, password, verify, timeout
        self.token = None

    def _request(self, method, path, retry=True, **kwargs):
        if self.token is None:
            self._login()
        headers = {"Authorization": f"Token {self.token}"}
        response = requests.request(method, f"{BASE}{path}", headers=headers, verify=self.verify, timeout=self.timeout, **kwargs)
        if response.status_code == 401 and retry and response.json().get("code") in RELOGIN_CODES:
            self.token = None
            return self._request(method, path, retry=False, **kwargs)      # bir marta qayta login
        if response.status_code == 429:
            time.sleep(int(response.headers.get("Retry-After", "5")))
            return self._request(method, path, retry=retry, **kwargs)
        return response

    def _login(self):
        response = requests.post(f"{BASE}/token/", json={"login": self.login, "password": self.password},
                                 verify=self.verify, timeout=self.timeout)
        response.raise_for_status()           # 401 invalid_credentials / 403 not_a_timekeeper -> istisno
        self.token = response.json()["token"]

    def employees(self, **params):
        return self._request("GET", "/employees/", params=params)

    def employee(self, tabel):
        return self._request("GET", f"/employees/{tabel}/")

    def update(self, tabel, fields):
        """fields — lug'at: {"position": "…", "status": "vacation"}"""
        return self._request("PATCH", f"/employees/{tabel}/", json=fields)


client = TimekeeperClient("ali_login", "…")
response = client.update("102", {"position": "Бош муҳандис", "status": "vacation", "replacement_tabel_number": "101"})
if response.status_code == 200:
    print(response.json()["status_label"])            # Меҳнат таътилида
elif response.status_code == 400:
    print(response.json())                            # {"maydon": ["xabar", …]}
else:
    print(response.status_code, response.json())      # {"detail": …, "code": …}
```

### 9.3 JavaScript (Node 18+ / brauzer)

```js
const BASE = "https://192.168.101.6:5000/api/v1/timekeeper";

async function login(login, password) {
  const res = await fetch(`${BASE}/token/`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ login, password }),
  });
  if (!res.ok) throw Object.assign(new Error("login failed"), { body: await res.json() });
  return (await res.json()).token;
}

async function updateEmployee(token, tabel, fields) {
  const res = await fetch(`${BASE}/employees/${encodeURIComponent(tabel)}/`, {
    method: "PATCH",
    headers: { Authorization: `Token ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
  return { status: res.status, body: await res.json() };
}
```

> Brauzerdan to'g'ridan-to'g'ri chaqirilsa, ES serverida tashqi tizim manzili `CORS_EXTRA_ORIGINS` muhit o'zgaruvchisiga
> qo'shilgan bo'lishi kerak. Server-server (backend) chaqiruvlarida CORS kerak emas — **tavsiya etilgan yo'l shu**.

## 10. Xavfsizlik va integratsiya bo'yicha tavsiyalar

- **Faqat HTTPS.** ES hozir o'z-o'zidan imzolangan sertifikat bilan ishlashi mumkin: sertifikatni tashqi tizimning ishonchli
  ro'yxatiga qo'shing (tavsiya). Faqat ichki tarmoqda, vaqtinchalik tekshiruvni o'chirish (`-k`, `verify=False`) mumkin.
- **Tokenni sir saqlang**: logga yozmang, URL'ga qo'ymang, kodga qotirmang. Token — табелчining huquqi bilan teng.
- **Табелчining parolini saqlamaslikka harakat qiling.** Eng yaxshisi: табелчи tizimga kirganda login/parolni bir marta kiritadi, siz
  token olib, uni xotirada (yoki sir ombori)da saqlaysiz. Parol saqlashga to'g'ri kelsa — faqat shifrlangan sir omborida.
- `401` (`token_expired`, `token_invalid`, `password_changed`) → qayta login; `403 not_a_timekeeper` → qayta login **yordam bermaydi**.
- `429` → `Retry-After` kuting; so'rovlarni ketma-ket yuborishda chegara (120/daq.) hisobga olinsin.
- `PATCH` **idempotent**: bir xil so'rovni takrorlash xavfsiz. Tarmoq xatosida qayta yuborish mumkin.
- Sinxronlash uchun `GET /employees/?updated_since=<oxirgi sinxronlash vaqti>` ishlating. `updated_at` ES'da **istalgan** o'zgarishda
  (veb-sahifadan ham) yangilanadi.
- Har bir yozuv ES serverining xavfsizlik jurnaliga tushadi: kim (login), qaysi xodim (табель), qaysi **maydonlar** — qiymatlarsiz.
- Xato haqida xabar berganda yuboring: so'rov vaqti, URL va metod, табель raqami, javobning HTTP kodi, `code` va matni.
  **Tokenni yubormang.**

## 11. Bu API'da yo'q narsalar

- Xodim **qo'shish** va **o'chirish**.
- Fotosuratni almashtirish (Face ID etaloni) — veb-interfeys orqali.
- Табель raqami, цех, login, parol, rolni o'zgartirish.
- Boshqa цех xodimlari, цехlar va бўлимlarning o'zi (qo'shish/o'zgartirish).

Zarur bo'lsa, ularni alohida, xavfsizlik nuqtai nazaridan ko'rib chiqib qo'shish mumkin.

## 12. Server tomoni sozlamalari (administrator uchun)

`.env` fayli (qiymatlar sukut bo'yicha ko'rsatilgan):

| O'zgaruvchi | Sukut | Ma'nosi |
|---|---|---|
| `TIMEKEEPER_API_TOKEN_TTL_HOURS` | `24` | Token amal qilish muddati (soat) |
| `TIMEKEEPER_API_MAX_TOKENS` | `5` | Bir foydalanuvchining faol tokenlari soni |
| `TIMEKEEPER_TOKEN_THROTTLE_RATE` | `10/min` | Token olish chegarasi (IP bo'yicha) |
| `TIMEKEEPER_API_THROTTLE_RATE` | `120/min` | API chegarasi (foydalanuvchi bo'yicha) |
| `LOGIN_FAILURE_LIMIT`, `LOGIN_LOCKOUT_SECONDS` | `5`, `900` | Noto'g'ri parol chegarasi (veb-kirish bilan umumiy) |
| `CORS_EXTRA_ORIGINS` | — | Brauzerdan chaqiriladigan tashqi tizim manzillari |

Tokenlarni Django admin'dagi «API токенлар (табелчи)» bo'limidan ko'rish va bekor qilish mumkin (token matni saqlanmaydi, faqat xesh).
Parol o'zgartirilsa yoki hisob o'chirilsa, shu foydalanuvchining barcha tokenlari avtomatik yaroqsiz bo'ladi.
Yangi o'zgarish bazaga jadval qo'shadi (`manage.py migrate`, docker'da avtomatik).
