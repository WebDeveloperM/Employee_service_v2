# Tabelchi API — employee_service (ES) bilan tizimlararo integratsiya

Bu hujjat **boshqa tizim** (keyingi o'rinlarda «sizning tizim») dasturchilari uchun. Unda ikkita ish uchun API bor:

1. **Tabelchini tayinlash va olib tashlash.** Qaysi xodim qaysi sexning tabelchisi — buni endi **sizning tizim** hal qiladi
   va ES'ga xabar beradi. ES'da tabelchi tayinlash sahifasi **yo'q** (olib tashlangan).
2. **Tabelchi nomidan xodimlarni ko'rish va yangilash.** Tabelchi sizning tizimingizda ishlaydi; sizning tizim ES'ga
   uning **nomidan** murojaat qiladi: tabelchi faqat o'z sexi xodimlarini ko'radi va ularning ma'lumotini yangilaydi.

Hujjatdagi hamma narsa koddagi holatga mos: har bir manzil, maydon, holat va xato kodi testlar bilan tekshiriladi.

- Manzil: `https://192.168.101.6:5000/api/v1/`
- Interaktiv hujjat (Swagger): `https://192.168.101.6:5000/api/docs/` (`timekeeper-assignments` va `timekeeper` bo'limlari)
- Format: JSON (`Content-Type: application/json`). `PUT` va `PATCH` uchun `multipart/form-data` ham qabul qilinadi.
- Vaqt: ISO 8601, Toshkent vaqti (`+05:00`). Xodim sanalari (tug'ilgan kun va h.k.) **`DD/MM/YYYY`** ko'rinishida qaytadi.
- **URL'dagi kirill harflari protsent-kodlangan bo'lishi shart** (`?search=%D0%91%D0%BE%D0%B1%D1%83%D1%80`). Brauzer va
  `requests`/`fetch` buni o'zi qiladi; `curl` da `-G --data-urlencode "search=Бобур"` ishlating. Kodlanmagan kirill harfli URL
  server tomonidan `400` bilan rad etiladi. So'rov **tanasi** (JSON) esa oddiy UTF-8 bo'ladi.
- Xato matnlari kirill yoki ingliz tilida bo'lishi va o'zgarishi mumkin. Umumiy xatolarda mashina uchun barqaror
  **`code`** bor — **matnga emas, `code`ga tayaning**. Maydon xatolarida (`{"maydon": ["…"]}`) maydon nomiga tayaning.

## 0. O'zgarishlar (2026-11-26): tabelchi ES'ga login-parol bilan kirmaydi

Tabelchi endi **faqat sizning tizimda** ishlaydi. Uni sizning tizim o'zi tekshiradi (masalan, E-IMZO bilan); ES tabelchining
login-parolini so'ramaydi, bilmaydi va unga ES'ga kirish bermaydi.

| Nima o'zgardi | Avval | Endi |
|---|---|---|
| ES veb-saytida tabelchi | ES hisobi bor tabelchiga «Табелчи» roli berilardi, u login-parol bilan kirib «Цех ходимлари» sahifasida xodimlarni o'zgartirardi | «Табелчи» roli va «Цех ходимлари» sahifasi **yo'q**. Ilgari tabelchi bo'lgan hisoblar oddiy «Фойдаланувчи» bo'ldi (faqat o'z kartochkasi) |
| Tabelchi obyekti (3.2) | `has_es_account` maydoni bor edi | `has_es_account` **olib tashlandi** |
| Tayinlash xatolari (3.5) | `409 privileged_account` — administrator/HR hisobiga bog'langan xodim tabelchi bo'la olmasdi | `privileged_account` **olib tashlandi**: tayinlov hisob roliga tegmaydi, shuning uchun bunday xodim ham tabelchi bo'la oladi |
| Tayinlov va ES hisobi | Tayinlov hisobga rol berardi; ES'da hisob rolini o'zgartirish yoki hisobni o'chirish tayinlovni o'chirardi | Tayinlov ES hisobiga **bog'liq emas**: na rol beradi, na hisob o'zgarishidan o'chadi (faqat xodim ES'da o'chirilsa o'chadi, 7-bo'lim) |
| O'rinbosarni «kim tayinlagan» (5.2) | Tabelchining ES hisobi yozilardi | ES hisobiga bog'lanmaydi; kim qilgani ES xavfsizlik jurnalida tabelchi tabel raqami bilan yoziladi. API javobi o'zgarmadi |

**O'zgarmadi:** autentifikatsiya (faqat xizmat kaliti, 2-bo'lim), barcha manzillar, so'rov va javob maydonlari (`has_es_account` dan
tashqari), qolgan xato kodlari, `is_protected` (administrator/HR xodimini tabelchi nomidan o'zgartirib bo'lmaydi).

**Sizning tizimda qilinadigan ishlar:**

1. `has_es_account` ni o'qiyotgan joy bo'lsa — olib tashlang (maydon endi kelmaydi).
2. `privileged_account` ni qayta ishlash kodi zarar qilmaydi, lekin bu kod endi kelmaydi; foydalanuvchiga «administrator/HR
   tabelchi bo'la olmaydi» deb ko'rsatadigan matn bo'lsa — olib tashlang.
3. Tabelchi foydalanuvchini **o'zingiz** tekshiring (E-IMZO) va ES'ga faqat **uning o'z** tabel raqamini yuboring (9-bo'lim):
   ES kimligini tekshirmaydi, faqat u ishlab turgan tabelchi ekanini va sex chegarasini.

## 1. Bir qarashda

```
SIZNING TIZIM                                   ES
─────────────                                   ──
Tabelchi tayinlandi/almashdi  ───────────────►  PUT    /timekeeper-assignments/{department_id}/   {"tabel_number": "2590"}
Tabelchi olib tashlandi       ───────────────►  DELETE /timekeeper-assignments/{department_id}/
Vaqti-vaqti bilan solishtirish ──────────────►  GET    /timekeeper-assignments/

Tabelchi ishlayapti (sizning tizimda):
  xodimlar ro'yxati            ───────────────►  GET    /timekeeper/{timekeeper_tabel}/employees/
  xodim kartochkasi            ───────────────►  GET    /timekeeper/{timekeeper_tabel}/employees/{tabel_number}/
  xodimni yangilash            ───────────────►  PATCH  /timekeeper/{timekeeper_tabel}/employees/{tabel_number}/
```

- **`timekeeper_tabel`** — tabelchining o'z **tabel raqami** (ES'dagi xodim tabel raqami bilan bir xil).
  Sizning tizimdagi har bir tabelchi foydalanuvchi ES'dagi xodimga shu raqam orqali bog'langan bo'lishi kerak.
- **`tabel_number`** — yangilanayotgan xodimning tabel raqami.
- Autentifikatsiya: **faqat xizmat kaliti** (2-bo'lim). Tabelchining ES login-paroli kerak emas va so'ralmaydi:
  tabelchi ES'ga kirmaydi (ES'da tabelchi roli ham, tabelchi sahifalari ham yo'q).

### Kim nimani hal qiladi

| Savol | Javob beradi |
|---|---|
| Qaysi xodim qaysi sexning tabelchisi? | **Sizning tizim** (`PUT`/`DELETE` bilan ES'ga yetkazadi) |
| Tizimga kirgan foydalanuvchi haqiqatan shu tabelchimi? | **Sizning tizim** (o'z autentifikatsiyasi, masalan E-IMZO) |
| Bu tabelchi hozir ishlayaptimi (ES'da tayinlangan va faol xodim)? | **ES** tekshiradi, har so'rovda |
| Tabelchi qaysi xodimlarga tegishi mumkin va qaysi maydonlarni o'zgartiradi? | **ES** cheklaydi (sex va maydonlar ro'yxati), kalit bo'lsa ham |

## 2. Autentifikatsiya

Har bir so'rovga **xizmat kalitini** qo'shing — quyidagilardan biri:

```http
X-Employee-Service-Key: <KALIT>
```
```http
Authorization: Bearer <KALIT>
```

- Kalit ES serverining `.env` faylidagi `EMPLOYEE_SERVICE_API_KEYS` ro'yxatida bo'lishi kerak. Ro'yxat vergul bilan
  ajratiladi: **sizning tizim uchun alohida kalit** oling (`kalit_TB,kalit_sizning_tizim`), shunda uni boshqalarga
  tegmasdan almashtirish yoki o'chirish mumkin. Ishlab chiqarishda kalit uzun tasodifiy qiymat bo'lishi shart (≥ 32 belgi).
- **Kalitni sir saqlang.** U faqat shu API'lar uchun emas: kalit bilan ES'ning boshqa `/api/v1/` manzillarida ham ma'lumot
  yozish mumkin (TB shunday ishlaydi). Kalitni kodga, brauzerga, logga, URL'ga qo'ymang — faqat sizning **server tomoningizda** saqlang.
- Veb-sessiya (ES saytiga kirgan administrator ham) bu API'larda **ishlamaydi**.
- Kalit yo'q → `401`, `code: not_authenticated`. Kalit noto'g'ri → `401`, `code: authentication_failed`.
  Ikkalasida ham `WWW-Authenticate: Bearer realm="employee-service"` sarlavhasi bor.
- So'rovlar soniga cheklov **yo'q**.

## 3. Tabelchini tayinlash

### 3.1 Qoidalar

- Har bir sexda **bitta** tabelchi bo'ladi. Yangi tayinlash eskisini **almashtiradi**.
- Xodim faqat **bitta** sexning tabelchisi bo'la oladi.
- Tabelchi — **o'sha sexning o'z faol xodimi** (`employee.department` = shu sex). Boshqa sex xodimini tayinlab bo'lmaydi.
- «Раҳбарият» sexiga tabelchi tayinlanmaydi va «Раҳбарият» xodimi tabelchi bo'la olmaydi.
- Tayinlov **ES hisobiga (login, parol, rol) bog'liq emas**: ES hisob yaratmaydi, mavjud hisobning roliga tegmaydi, xodimda
  hisob bor-yo'qligi ahamiyatsiz. Tabelchi ES veb-saytiga tabelchi sifatida kirmaydi — u faqat sizning tizim orqali ishlaydi.
- Operatsiyalar **atomik**: xato bo'lsa hech narsa o'zgarmaydi.

### 3.2 Tabelchi obyekti

```json
{
  "employee_id": 1,
  "tabel_number": "101",
  "full_name": "Валиев Али",
  "position": "Фаррош",
  "is_active": true,
  "assigned_at": "2026-11-25T16:41:08.083103+05:00"
}
```

| Maydon | Ma'nosi |
|---|---|
| `tabel_number` | Tabelchi xodimning tabel raqami — `timekeeper_tabel` sifatida shuni ishlating |
| `is_active` | `false` — xodim ES'da faol emas yoki o'chirilgan. U **nomidan ish qilib bo'lmaydi** (`not_a_timekeeper`); yangi tabelchi tayinlang |

### 3.3 `GET /api/v1/timekeeper-assignments/` — barcha sexlar va ularning tabelchilari

Sexlar «Штат бирлиги ва лавозим» daraxtidagi tartibda (Раҳбарият, 20-Цех, keyin qolganlari). Sexlar soni kam, sahifalash yo'q.
Solishtirish (reconcile) uchun ham, **sex `id` larini bilish uchun ham** shu ro'yxatni ishlating.

| Parametr | Ma'nosi |
|---|---|
| `assigned` | `true` — faqat tabelchisi borlar, `false` — tabelchisi yo'qlar |
| `tabel_number` | Shu tabel raqamli xodim tabelchi bo'lgan sex |

```json
{
  "count": 2,
  "results": [
    {
      "department": {"id": 1, "name": "1-Цех Биринчи"},
      "timekeeper": {"employee_id": 1, "tabel_number": "101", "full_name": "Валиев Али", "position": "Фаррош",
                     "is_active": true, "assigned_at": "2026-11-25T16:41:08.083103+05:00"},
      "employee_count": 2
    },
    {"department": {"id": 2, "name": "2-Цех Иккинчи"}, "timekeeper": null, "employee_count": 1}
  ]
}
```

`employee_count` — sexdagi faol xodimlar soni. Noto'g'ri `assigned` qiymati — `400` (`{"assigned": ["…"]}`).

### 3.4 `GET /api/v1/timekeeper-assignments/{department_id}/` — bitta sex

```json
{"department": {"id": 2, "name": "2-Цех Иккинчи"}, "timekeeper": null}
```

Sex yo'q → `404`, `code: department_not_found`.

### 3.5 `PUT /api/v1/timekeeper-assignments/{department_id}/` — tabelchi tayinlash

```http
PUT /api/v1/timekeeper-assignments/2/
X-Employee-Service-Key: …
Content-Type: application/json

{"tabel_number": "201"}
```

`200 OK`:

```json
{
  "department": {"id": 2, "name": "2-Цех Иккинчи"},
  "timekeeper": {"employee_id": 3, "tabel_number": "201", "full_name": "Каримов Сардор", "position": "Фаррош",
                 "is_active": true, "assigned_at": "2026-11-25T16:41:08.097977+05:00"},
  "replaced": null,
  "changed": true
}
```

| Maydon | Ma'nosi |
|---|---|
| `replaced` | Almashtirilgan eski tabelchi `{"tabel_number", "full_name"}`; almashtirish bo'lmasa `null` |
| `changed` | `false` — bu xodim allaqachon shu sexning tabelchisi edi (hech narsa o'zgarmadi) |

**Idempotent**: bir xil so'rovni takrorlash zararsiz (`changed: false`) — tarmoq xatosida qayta yuboring.

Xatolar:

| HTTP | `code` | Sabab |
|---|---|---|
| 400 | — | `tabel_number` yuborilmagan yoki bo'sh (`{"tabel_number": ["…"]}`) |
| 404 | `department_not_found` | Bunday sex yo'q |
| 404 | `employee_not_found` | Bunday tabel raqamli xodim yo'q yoki o'chirilgan |
| 409 | `employee_inactive` | Xodim faol emas |
| 409 | `leadership_department` | «Раҳбарият» sexi yoki uning xodimi |
| 409 | `already_timekeeper_elsewhere` | Xodim boshqa sexning tabelchisi. Javobda `department` — qaysi sex |
| 409 | `employee_not_in_department` | Xodim bu sexga tegishli emas |

`409` javobi:

```json
{"detail": "Бу ходим аллақачон «1-Цех Биринчи» цехининг табелчиси.", "code": "already_timekeeper_elsewhere",
 "department": {"id": 1, "name": "1-Цех Биринчи"}}
```

**Tabelchini boshqa sexga o'tkazish:** avval eski sexda `DELETE` qiling (yoki o'sha sexga boshqa tabelchi tayinlang), xodimning sexi ES'da
o'zgarganidan keyin yangi sexda `PUT` qiling. Aks holda `already_timekeeper_elsewhere` / `employee_not_in_department` keladi.

### 3.6 `DELETE /api/v1/timekeeper-assignments/{department_id}/` — tabelchini olib tashlash

`204 No Content`, tanasi yo'q. **Idempotent**: sexda tabelchi bo'lmasa ham `204`. Sex yo'q → `404` (`department_not_found`).
Xodimning ES'dagi ma'lumoti va hisobi (bo'lsa) o'zgarmaydi.

## 4. Tabelchi nomidan ishlash

Hamma manzil `/api/v1/timekeeper/{timekeeper_tabel}/…` ostida. Har bir so'rovda ES **avval kalitni**, keyin
`{timekeeper_tabel}` **hozir ishlayotgan tabelchi ekanini** tekshiradi (ES'da tayinlangan, xodim faol va o'chirilmagan).
Aks holda — `403`, `code: not_a_timekeeper`:

```json
{"detail": "Бу табель рақамли ходим ишлаб турган табелчи эмас: цехга тайинланмаган, ёки фаол эмас/ўчирилган.", "code": "not_a_timekeeper"}
```

Bu javob ma'nosi: **sizning va ES'ning tabelchi haqidagi ma'lumoti farq qilyapti**. Foydalanuvchiga «ruxsat yo'q» ko'rsating va
3.3-bo'limdagi ro'yxat bilan solishtirishni (7-bo'lim) ishga tushiring.

Tabelchi faqat **o'z sexining** xodimlarini ko'radi. Boshqa sex, o'chirilgan yoki mavjud bo'lmagan xodim uchun javob bir xil:
`404`, `code: not_found` (xodimning mavjudligi ham bildirilmaydi).

### 4.1 `GET /api/v1/timekeeper/{timekeeper_tabel}/` — tabelchi va uning sexi

Tabelchi ishlab turganini tez tekshirish uchun (masalan, tabelchi sahifasini ochishdan oldin):

```json
{
  "department": {"id": 1, "name": "1-Цех Биринчи"},
  "timekeeper": {"employee_id": 1, "tabel_number": "101", "full_name": "Валиев Али", "position": "Фаррош",
                 "is_active": true, "assigned_at": "2026-11-25T16:41:08.083103+05:00"}
}
```

### 4.2 `GET /api/v1/timekeeper/{timekeeper_tabel}/reference/` — ma'lumotnomalar

Yangilash formasini to'ldirish uchun `id` va qiymatlar:

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

`sections` — **faqat tabelchining o'z sexi** bo'limlari (ichma-ich bo'lim ota-bo'limidan keyin keladi).
Toifalar va ish tartiblari umumiy ro'yxat.

### 4.3 `GET /api/v1/timekeeper/{timekeeper_tabel}/employees/` — xodimlar ro'yxati

Faqat tabelchining sexidagi o'chirilmagan xodimlar. Tartib «Штат бирлиги ва лавозим» sahifasidagi kabi: bo'limlar «ID бўлим»
bo'yicha, ichida lavozim tartibi, keyin familiya.

| Parametr | Ma'nosi |
|---|---|
| `search` | Ism, familiya, otasining ismi, tabel yoki lavozimdan qidiradi. Bir nechta so'z — hammasi mos kelishi kerak |
| `tabel_number` | Aniq tabel raqami |
| `status` | Bitta holat: `working`, `vacation`, `business_trip`, `sick_leave`, `childcare_leave`, `over_staff`, `inactive` |
| `updated_since` | ISO 8601 vaqt: shundan keyin o'zgargan xodimlar (sinxronlash uchun), masalan `2026-11-25T09:00:00+05:00` |
| `page`, `page_size` | Sahifalash. `page_size` 1–200, sukut bo'yicha 50 |

```json
{
  "count": 2,
  "next": "https://192.168.101.6:5000/api/v1/timekeeper/101/employees/?page=2&page_size=1",
  "previous": null,
  "results": [ { "…": "xodim obyekti (4.5-bo'lim)" } ]
}
```

Noto'g'ri `status` yoki `updated_since` — `400` (`{"status": ["…"]}`). Sahifa mavjud emas — `404` (`not_found`).

### 4.4 `GET /api/v1/timekeeper/{timekeeper_tabel}/employees/{tabel_number}/` — bitta xodim

`{tabel_number}` — xodimning tabel raqami (matn, masalan `102`; URL'da `/` bo'lmasligi kerak).

### 4.5 Xodim obyekti

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

- `status` — xodimning joriy holati (asosiy maydon). `is_active` va `work_status` undan kelib chiqadi, faqat o'qish uchun.
- `replacement` — shu xodimning o'rniga ishlayotgan xodim (`null` — yo'q).
- `is_protected: true` — administrator yoki HR xodimi. Uni **o'zgartirib bo'lmaydi** (PATCH → `403`, `permission_denied`).
- Sanalar `DD/MM/YYYY`, bo'lmasa `null`.
- Javobda **parol, login, rol va ichki integratsiya maydonlari yo'q**.
- Fotosurat havolalari (`base_image_url`, `base_image_thumb_url`) — faqat ko'rsatish uchun; rasm ES serveridan yuklanadi.

## 5. Xodimni yangilash — `PATCH /api/v1/timekeeper/{timekeeper_tabel}/employees/{tabel_number}/`

Faqat **yuborilgan** maydonlar o'zgaradi. Muvaffaqiyatda `200` va yangilangan xodim obyekti qaytadi.
Amal **butunlay bajariladi yoki butunlay rad etiladi**: xato bo'lsa hech narsa saqlanmaydi.
**Idempotent**: bir xil so'rovni takrorlash xavfsiz. `PUT` va `DELETE` yo'q (`405`, `method_not_allowed`).

```http
PATCH /api/v1/timekeeper/101/employees/102/
X-Employee-Service-Key: …
Content-Type: application/json

{"position": "Бош муҳандис", "status": "vacation", "replacement_tabel_number": "101"}
```

### 5.1 O'zgartirish mumkin bo'lgan maydonlar

| Maydon | Tur / format | Qoida |
|---|---|---|
| `first_name` | matn, ≤ 255 | Bo'sh bo'lmasin |
| `last_name` | matn, ≤ 255 | Bo'sh bo'lmasin |
| `surname` | matn, ≤ 255 | Bo'sh bo'lishi mumkin |
| `gender` | `"M"`, `"F"` yoki `""` | Ma'lumotnoma: `genders` |
| `date_of_birth` | sana yoki `null` | `DD.MM.YYYY`, `DD/MM/YYYY` yoki `YYYY-MM-DD`; `null` — tozalash |
| `phone_number_1`, `phone_number_2` | matn, ≤ 20 belgi | Tavsiya: `+998 90 123 45 67`; `""` — tozalash |
| `section_id` | son yoki `null` | `reference/` dagi `sections[].id`, **faqat o'z sexidan**; `null` — bo'limsiz |
| `position` | matn | Lavozim nomi. Shtat qatori bilan nom bo'yicha moslanadi (bo'shliq va harf registri hisobga olinmaydi) |
| `grade` | `"4"`, `"12"`, `"12*"` | 1–2 xonali raqam, oxirida ixtiyoriy `*`. `"04*"` → `"4*"`. `""`/`null` — tozalash |
| `date_of_employment` | sana yoki `null` | Sana formatlari yuqoridagidek |
| `date_of_change_position` | sana yoki `null` | Sana formatlari yuqoridagidek |
| `position_category_id` | son yoki `null` | `reference/` → `position_categories[].id` |
| `employee_category_id` | son yoki `null` | `reference/` → `employee_categories[].id` |
| `work_schedule_id` | son yoki `null` | `reference/` → `work_schedules[].id` |
| `status` | matn | `working`, `vacation`, `business_trip`, `sick_leave`, `childcare_leave`, `over_staff`, `inactive` (5.2) |
| `replacement_tabel_number` | matn yoki `null` | O'rinbosarning tabel raqami (5.2); `null` — o'rinbosarni olib tashlash |
| `special_clothing_size`, `clothe_size`, `shoe_size`, `jacket_size`, `tshirt_size` | matn, ≤ 255 | O'lchamlar, erkin matn |

### 5.2 Holat (`status`) va o'rinbosar

`status` ES veb-formasidagi «Ҳолати» bilan bir xil:

| `status` | Ma'nosi | `is_active` |
|---|---|---|
| `working` | Фаол (ishda) | `true` |
| `vacation` | Меҳнат таътилида | `true` |
| `business_trip` | Хизмат сафарида | `true` |
| `sick_leave` | Касаллик таътилида | `true` |
| `childcare_leave` | Бола парваришлаш таътилида | `true` |
| `over_staff` | св.шт. (штатдан ташқари) | `true` |
| `inactive` | Фаол эмас (ishdan chiqqan) | `false` — xodimning ES hisobi ham o'chiriladi (agar bo'lsa) |

O'rinbosar (`replacement_tabel_number`) — xodim vaqtincha ishda bo'lmaganda uning o'rniga ishlaydigan xodim:

- `working` holatida o'rinbosar bo'lmaydi: `working`ga qaytarilsa mavjud o'rinbosar yozuvi avtomatik o'chadi.
- O'rinbosar **shu sexdan**, **faol**, o'zi ishda (`working` yoki `over_staff`) bo'lishi va xodimning o'zi bo'lmasligi shart.
  Aks holda `400` va `{"replacement_tabel_number": ["…"]}`.
- Xodimga **bittadan** o'rinbosar qo'yiladi (yangisi eskisini almashtiradi); bitta xodim bir nechta xodimning o'rniga ishlashi mumkin.
- Holatni o'zgartirmasdan faqat o'rinbosarni olib tashlash: `{"replacement_tabel_number": null}`.
- Kim tayinlagani ES xavfsizlik jurnalida tabelchining tabel raqami bilan yoziladi (ES hisobiga bog'lanmaydi).

### 5.3 O'zgartirib bo'lmaydigan maydonlar

Bularni yuborsangiz `400` qaytadi va **hech narsa saqlanmaydi** (javobda har bir maydon uchun sabab bor):

| Maydon | Sabab |
|---|---|
| `tabel_number` | Xodimning kaliti, o'zgarmaydi |
| `department_id`, `department` | Xodim o'z sexida qoladi |
| `is_active`, `work_status` | `status` maydonidan foydalaning |
| `replacement_employee_id` | `replacement_tabel_number` dan foydalaning |
| `base_image` | Fotosurat — Face ID etaloni. Faqat ES veb-interfeysidan o'zgaradi |
| `login`, `password`, `password_confirm`, `role` | Hisob bu API orqali o'zgarmaydi |
| boshqa har qanday noma'lum maydon (`slug`, `metadata`, `source_system`, …) | Umumiy xabar: «Номаълум ёки ўзгартириш мумкин бўлмаган майдон» |

### 5.4 Tekshiruv tartibi va xato formati

1. Avval **maydon nomlari** tekshiriladi (5.3): barcha rad etilgan nomlar bir javobda qaytadi.
2. So'ng har bir **maydon qiymati** tekshiriladi: barcha xato maydonlar bir javobda qaytadi.
3. Maydonlar to'g'ri bo'lgandagina **maydonlararo** qoidalar tekshiriladi (masalan, `section_id` shu sexga tegishlimi).
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

Umumiy xatolar (`detail` + `code`) — 6-bo'lim.

## 6. Xatolar: HTTP holatlari va `code`

Maydon xatolaridan (`400`) boshqa hamma xato `{"detail": "…", "code": "…"}` shaklida:

| HTTP | `code` | Qachon |
|---|---|---|
| 400 | `empty_update` | `PATCH` tanasi bo'sh (`{}`) |
| 400 | `parse_error` | Tana JSON obyekt emas (massiv yoki buzuq JSON) |
| 401 | `not_authenticated` | Xizmat kaliti yuborilmagan |
| 401 | `authentication_failed` | Xizmat kaliti noto'g'ri |
| 403 | `not_a_timekeeper` | `{timekeeper_tabel}` ishlab turgan tabelchi emas (4-bo'lim) |
| 403 | `permission_denied` | Administrator/HR xodimini o'zgartirishga urinish (`is_protected: true`) |
| 404 | `department_not_found` | Sex yo'q (`timekeeper-assignments`) |
| 404 | `employee_not_found` | `PUT` da ko'rsatilgan tabel raqamli xodim yo'q |
| 404 | `not_found` | Xodim yo'q, o'chirilgan yoki boshqa sexda; yoki noto'g'ri `page` |
| 405 | `method_not_allowed` | Ruxsat etilmagan HTTP metod |
| 409 | `employee_inactive`, `leadership_department`, `already_timekeeper_elsewhere`, `employee_not_in_department` | Tayinlash qoidalari (3.5-bo'lim) |
| 415 | `unsupported_media_type` | Qo'llab-quvvatlanmaydigan `Content-Type` |
| 5xx | — | Server xatosi. Biroz kutib qayta urinib ko'ring; takrorlansa xabar bering |

## 7. Sinxronlash (reconcile) — nimaga va qanday

Tabelchi haqidagi ma'lumot ikki joyda turadi (sizda va ES'da), shuning uchun vaqti-vaqti bilan solishtiring:
ishga tushganda, kuniga bir necha marta yoki `403 not_a_timekeeper` kelganda.

ES'da tayinlovga siz bilmagan holda faqat xodimning o'zi ta'sir qiladi:

- xodim ES'da o'chirilsa — tayinlov o'chadi;
- xodim faol bo'lmay qolsa — tayinlov **qoladi**, lekin `is_active: false` bo'ladi va uning nomidan ish qilib bo'lmaydi.

ES hisobini (login, parol, rol) o'zgartirish yoki o'chirish tayinlovga **ta'sir qilmaydi**.

Algoritm:

1. `GET /api/v1/timekeeper-assignments/` — ES'dagi holat.
2. Har bir sex uchun sizdagi kerakli tabelchi bilan solishtiring.
3. Farq bo'lsa: kerak bo'lsa `PUT`, kerak bo'lmasa `DELETE` (ikkalasi ham idempotent).
4. `is_active: false` tabelchilarni yangi tabelchi tayinlash yoki sexni «tabelchisiz» deb belgilash uchun ishlating.

## 8. Kod namunalari

### 8.1 Python (`requests`)

```python
import time
from urllib.parse import quote

import requests


class EsError(Exception):
    """ES xato qaytardi. status — HTTP kod, code — barqaror belgi (yo'q bo'lishi mumkin), body — javob tanasi."""

    def __init__(self, status, body):
        self.status, self.body = status, body
        self.code = body.get("code") if isinstance(body, dict) else None
        super().__init__(f"{status} {body}")


class EmployeeService:
    def __init__(self, base_url, api_key, verify=True, timeout=30, retries=2):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.session = requests.Session()
        self.session.headers["X-Employee-Service-Key"] = api_key        # kalit faqat serverda saqlanadi
        self.verify, self.timeout, self.retries = verify, timeout, retries

    def _call(self, method, path, **kwargs):
        # Hamma operatsiyalar idempotent: tarmoq xatosi va 5xx da xavfsiz qayta yuboriladi.
        for attempt in range(self.retries + 1):
            try:
                response = self.session.request(method, self.base + path, verify=self.verify, timeout=self.timeout, **kwargs)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == self.retries:
                    raise
            else:
                if response.status_code < 500 or attempt == self.retries:
                    break
            time.sleep(2 ** attempt)
        body = response.json() if response.content else None
        if response.status_code >= 400:
            raise EsError(response.status_code, body)
        return body

    # ---- tabelchi tayinlash -------------------------------------------------------------
    def assignments(self, **params):
        return self._call("GET", "/timekeeper-assignments/", params=params)["results"]

    def assign(self, department_id, tabel_number):
        return self._call("PUT", f"/timekeeper-assignments/{department_id}/", json={"tabel_number": tabel_number})

    def unassign(self, department_id):
        self._call("DELETE", f"/timekeeper-assignments/{department_id}/")

    # ---- tabelchi nomidan ---------------------------------------------------------------
    def _tk(self, timekeeper_tabel, path=""):
        return f"/timekeeper/{quote(str(timekeeper_tabel), safe='')}/{path}"

    def timekeeper(self, timekeeper_tabel):
        return self._call("GET", self._tk(timekeeper_tabel))

    def reference(self, timekeeper_tabel):
        return self._call("GET", self._tk(timekeeper_tabel, "reference/"))

    def employees(self, timekeeper_tabel, **params):
        """Barcha sahifalar bo'yicha generator."""
        page = self._call("GET", self._tk(timekeeper_tabel, "employees/"), params={"page_size": 200, **params})
        while True:
            yield from page["results"]
            if not page["next"]:
                return
            page = self._call("GET", page["next"].replace(self.base, "", 1))

    def employee(self, timekeeper_tabel, tabel_number):
        return self._call("GET", self._tk(timekeeper_tabel, f"employees/{quote(str(tabel_number), safe='')}/"))

    def update(self, timekeeper_tabel, tabel_number, fields):
        """fields — lug'at: {"position": "…", "status": "vacation"}"""
        return self._call("PATCH", self._tk(timekeeper_tabel, f"employees/{quote(str(tabel_number), safe='')}/"), json=fields)


es = EmployeeService("https://192.168.101.6:5000", "<KALIT>", verify=False)    # verify=False — faqat ichki tarmoq, 9-bo'lim

# tabelchi tayinlash (sizning tizimda tabelchi tayinlanganda):
departments = {item["department"]["name"]: item["department"]["id"] for item in es.assignments()}
es.assign(departments["1-Цех Биринчи"], "101")

# tabelchi (tabel 101) sizning tizimda xodimni yangilaydi:
try:
    employee = es.update("101", "102", {"position": "Бош муҳандис", "status": "vacation", "replacement_tabel_number": "101"})
    print(employee["status_label"])                       # Меҳнат таътилида
except EsError as error:
    if error.status == 400:
        print(error.body)                                 # {"maydon": ["xabar", ...]} — foydalanuvchiga ko'rsating
    elif error.code == "not_a_timekeeper":
        print("ES'da bu foydalanuvchi tabelchi emas: solishtirishni ishga tushiring")
    elif error.status == 404:
        print("Xodim topilmadi yoki boshqa sexda")
    else:
        raise
```

### 8.2 Solishtirish (reconcile) namunasi

```python
def reconcile(es, wanted):
    """wanted: {sex_id: tabel_raqami yoki None} — sizning tizimdagi holat."""
    actual = {
        item["department"]["id"]: item["timekeeper"] for item in es.assignments()
    }
    for department_id, tabel in wanted.items():
        current = actual.get(department_id)
        current_tabel = current["tabel_number"] if current else None
        if current_tabel == tabel and (current is None or current["is_active"]):
            continue
        if tabel is None:
            es.unassign(department_id)
        else:
            try:
                es.assign(department_id, tabel)
            except EsError as error:
                if error.code == "already_timekeeper_elsewhere":
                    print(f"{tabel} avval {error.body['department']['name']} sexidan olib tashlansin")
                elif error.status == 409:
                    print(f"{tabel}: {error.code}")          # xodim sexga tegishli emas, faol emas va h.k.
                else:
                    raise
```

### 8.3 curl

```bash
BASE=https://192.168.101.6:5000/api/v1
KEY=<KALIT>

# sexlar va tabelchilar
curl -sk "$BASE/timekeeper-assignments/" -H "X-Employee-Service-Key: $KEY"

# tabelchi tayinlash va olib tashlash
curl -sk -X PUT "$BASE/timekeeper-assignments/2/" -H "X-Employee-Service-Key: $KEY" \
     -H 'Content-Type: application/json' -d '{"tabel_number":"201"}'
curl -sk -X DELETE "$BASE/timekeeper-assignments/2/" -H "X-Employee-Service-Key: $KEY"

# tabelchi 101 nomidan: xodimlar ro'yxati va yangilash (kirill harflari URL'da kodlanishi shart: -G --data-urlencode)
curl -sk -G "$BASE/timekeeper/101/employees/" --data-urlencode "search=Бобур" -H "X-Employee-Service-Key: $KEY"
curl -sk -X PATCH "$BASE/timekeeper/101/employees/102/" -H "X-Employee-Service-Key: $KEY" \
     -H 'Content-Type: application/json' -d '{"position":"Бош муҳандис","phone_number_1":"+998 90 555 44 33"}'
```

## 9. Xavfsizlik va integratsiya bo'yicha tavsiyalar

- **Faqat HTTPS.** ES hozir o'z-o'zidan imzolangan sertifikat bilan ishlashi mumkin: sertifikatni sizning tizimning ishonchli
  ro'yxatiga qo'shing (tavsiya). Faqat ichki tarmoqda va vaqtincha tekshiruvni o'chirish (`-k`, `verify=False`) mumkin.
- **Kalit faqat server tomonida.** Brauzer/mobil ilovadan ES'ga to'g'ridan-to'g'ri murojaat qilmang: foydalanuvchi sizning
  tizimingizga kiradi, sizning serveringiz ES'ga murojaat qiladi. Shuning uchun ES'da CORS sozlash ham kerak emas.
- **Tabelchini o'zingiz aniqlang.** ES `timekeeper_tabel` ga ishonadi (kalit egasi sifatida): ES tabelchi kimligini
  tekshira olmaydi, faqat u ishlab turgan tabelchi ekanini va sexi chegarasini. Foydalanuvchini to'g'ri autentifikatsiya
  qilib (masalan, E-IMZO bilan), **uning o'z** tabel raqamini yuborish — sizning mas'uliyatingiz. Foydalanuvchidan
  (brauzerdan) kelgan tabel raqamini hech qachon qabul qilmang: uni foydalanuvchining hisobidan oling.
- **Ruxsatlar ES tomonida ham cheklangan:** kalit bilan ham tabelchi nomidan faqat uning sexi va 5.1-bo'limdagi maydonlar o'zgaradi.
- **Idempotent** so'rovlarni (`PUT`, `DELETE`, `PATCH`, `GET`) tarmoq xatosida qayta yuborish xavfsiz. «So'nggi yozgan g'olib»:
  bir vaqtda ikki tomon bir xodimni o'zgartirsa, oxirgisi saqlanadi.
- Har bir yozuv ES xavfsizlik jurnaliga tushadi: qaysi tabelchi nomidan, qaysi xodim, qaysi **maydonlar** (qiymatlarsiz), qaysi IP.
- Xato haqida xabar berganda yuboring: so'rov vaqti, URL va metod, tabel raqamlari, HTTP kod, `code` va matn. **Kalitni yubormang.**

## 10. Bu API'larda yo'q narsalar

- Xodim **qo'shish** va **o'chirish**.
- Fotosuratni almashtirish (Face ID etaloni) — ES veb-interfeysi orqali.
- Tabel raqami, sex, login, parol, rolni o'zgartirish.
- ES'da hisob (login/parol) yaratish: tabelchi uchun hisob kerak emas.
- Tabelchining ES veb-saytiga tabelchi sifatida kirishi: ES'da tabelchi roli va sahifalari yo'q.
- Boshqa sex xodimlari, sexlar va bo'limlarning o'zi (qo'shish/o'zgartirish).

Zarur bo'lsa, ularni alohida, xavfsizlik nuqtai nazaridan ko'rib chiqib qo'shish mumkin.

## 11. ES ma'muri uchun

- ES'da **tabelchi tayinlash sahifasi olib tashlangan** (menyudagi «Табелчилар» yo'q). Tayinlash faqat shu API orqali.
- Kalit: `.env` → `EMPLOYEE_SERVICE_API_KEYS=<mavjud kalit>,<yangi tizim uchun kalit>`; so'ng konteynerni **qayta yarating** (oddiy `docker restart` yangi `.env` qiymatlarini o'qimaydi): `docker compose up -d --force-recreate employee-service`.
- Yangi migratsiya (`0041`) ilgari qo'shilgan tabelchi-token jadvalini olib tashlaydi (`manage.py migrate`, docker'da avtomatik).
  Oldingi (token) variantdagi `/api/v1/timekeeper/token/`, `logout/`, `me/` manzillari **yo'q**.
- «Табелчи» roli va «Цех ходимлари» sahifasi **olib tashlangan**: tabelchi ES'ga login-parol bilan kirmaydi. Migratsiya
  `0042` ilgari tabelchi bo'lgan hisoblarni oddiy «Фойдаланувчи» qiladi (hisob va tayinlov qoladi; `manage.py migrate`, docker'da avtomatik).
- Hisob rolini o'zgartirish yoki hisobni o'chirish tayinlovga ta'sir qilmaydi. Xodimni o'chirish — tayinlovni ham o'chiradi (7-bo'lim).
