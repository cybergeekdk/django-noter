# Django noter

Et simpelt Django 6.1-projekt med en note-app (opret, vis, slet + admin).

Søgning, oprettelse og sletning sker med AJAX (`fetch`) uden at siden genindlæses,
og en loader vises mens forespørgsler kører. Uden JavaScript virker siden stadig
som almindelige formularer. Koden ligger i `notes/static/notes/notes.js`.

## Kom i gang

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser   # valgfrit, til /admin/
python manage.py runserver
```

Åbn http://127.0.0.1:8000/ — noter ligger på `/noter/` og admin på `/admin/`.

## Kategorier

Noter kan have en kategori (valgfri). En kategori har et navn og en farve (en af
Bootstrap-farverne), og vises som et farvet mærke på noten.

- På `/noter/` vælges kategori i formularen. Listen kan filtreres med dropdown'en eller
  ved at klikke på et mærke, og filteret virker sammen med søgningen
  (`/noter/?q=tekst&kategori=2`).
- Kategorier oprettes, omdøbes og slettes i admin (`/admin/notes/category/`). Hvis en
  kategori slettes, beholder noterne deres indhold og står blot uden kategori.
- Migrationen opretter tre standardkategorier: Privat, Arbejde og Idéer.
- Modellen er `notes.models.Category`; `Note.category` er en `ForeignKey` med `SET_NULL`.

## Guider (private)

Under **Guider** i menuen (kræver login) kan hver bruger lave større guider, som **kun
ejeren kan se**. En guide har titel, beskrivelse, et valgfrit forsidebillede og et antal
afsnit. Hvert afsnit har en titel, tekst i Markdown og et vilkårligt antal billeder med
billedtekst. Afsnit kan flyttes op og ned, og detaljesiden har en indholdsfortegnelse og
forstørrelse af billeder. Søgningen på `/guider/` kigger også i afsnittenes tekst.

Privatliv og sikkerhed:

- Alle visninger filtrerer på ejer. Andres guider og billeder giver **404**, også for
  super admins. Guider er bevidst ikke registreret i Djangos admin.
- Billeder gemmes i `private_media/` (git-ignoreret) med tilfældige filnavne og har
  ingen offentlig URL. De leveres kun af `guides.views.serve_image`/`serve_cover`, som
  tjekker ejerskab, med `nosniff` og en stram `Content-Security-Policy`.
- Uploads valideres på størrelse (maks. 8 MB), filendelse og **faktisk** billedformat
  (JPEG, PNG, GIF, WEBP). SVG afvises. Maks. 20 billeder pr. afsnit.
- Markdown renses med `nh3`: ingen scripts, iframes, billeder eller `javascript:`-links.
- Billedfiler slettes fra disken, når billedet, afsnittet eller guiden slettes.

Husk at tage backup af `private_media/` sammen med databasen. I produktion skal
`private_media/` ligge på vedvarende lager.

## Udstyr (privat)

Under **Udstyr** i menuen (kræver login) kan hver bruger føre en liste over det, man har:
navn, placering, antal, købsdato, garantiens udløbsdato, bemærkninger (fx serienummer) og
bilag (kvitteringer, manualer, fotos). Kun navn er påkrævet. Listen er **privat**: andre
brugere, også super admins, får 404.

- **Garantistatus** beregnes ud fra udløbsdatoen: *Udløbet*, *Udløber snart* (højst 60 dage
  tilbage, justeres med `WARRANTY_SOON_DAYS` i `inventory/models.py`), *Garanti OK* eller
  ingen garantidato. Overblikskortene øverst viser antal ting, stk. i alt og hvor mange
  garantier der udløber snart eller er udløbet (klik for at filtrere).
- **Søg, filtrér og sortér** på navn/placering/bemærkning, placering, garantistatus og
  navn, placering, antal, købsdato eller garanti.
- **Eksportér CSV** (`/udstyr/eksport/`) med de aktuelle filtre. Filen bruger semikolon og
  UTF-8 med BOM, så dansk Excel åbner den korrekt, og celler der starter med `=`, `+`, `-`
  eller `@` får et foranstillet `'` mod formelinjektion.

### Placeringer

Placeringer er faste (`/udstyr/placeringer/`): oprettes, omdøbes og slettes ét sted, og
tingene følger med. I tingens formular vælger man en eksisterende placering eller skriver
et nyt navn (så oprettes den, og samme navn med andre store/små bogstaver genbruges).
Sletter man en placering, beholdes tingene og står bagefter uden placering. Migrationerne
har flyttet tidligere tekstplaceringer over til faste placeringer.

### Bilag (kvitteringer)

Hver ting kan have op til 10 bilag: JPEG, PNG, GIF, WEBP eller PDF, op til 8 MB pr. fil.
Bilag gemmes privat i `private_media/` på samme måde som guide-billeder (tilfældige
filnavne, ingen offentlig URL, ejerskab tjekkes ved hver visning, indholdet valideres,
SVG afvises, filer slettes sammen med tingen). Billeder vises direkte; PDF'er hentes som
fil. Husk backup af `private_media/`.

### E-mailpåmindelser

Under **Påmindelser** (`/udstyr/paamindelser/`) slår man garantipåmindelser til, angiver sin
e-mailadresse og hvor mange dage før udløb man vil have besked (1 uge til 3 måneder), og kan
sende sig selv en testmail. Hver garanti giver **kun én mail**; ændrer man garantidatoen,
nulstilles påmindelsen. Mails sendes af en kommando, der skal køres dagligt:

```bash
python manage.py send_warranty_reminders            # send
python manage.py send_warranty_reminders --dry-run  # vis kun, hvor mange der ville blive sendt
```

Planlæg den fx med cron (hver dag kl. 08:00):

```cron
0 8 * * * cd /sti/til/django_projekt && .venv/bin/python manage.py send_warranty_reminders
```

Som standard skrives mails bare i terminalen (udvikling). Til rigtige mails sættes
miljøvariabler, som `mysite/settings.py` læser til Djangos `MAILERS`:

| Variabel | Betydning |
|---|---|
| `EMAIL_HOST` | SMTP-server (sættes denne, bruges SMTP) |
| `EMAIL_PORT`, `EMAIL_USE_TLS` | Standard `587` og `1` |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Login til SMTP |
| `DEFAULT_FROM_EMAIL` | Afsenderadresse |
| `SITE_URL` | Sitets adresse, bruges til links i mails (fx `https://noter.example.dk`) |

Sker en afsendelse ikke, prøves den igen ved næste kørsel.

## Elpriser

Siden **Elpriser** (`/elpriser/`, åben for alle, da det er offentlige data) viser danske
day-ahead-spotpriser fra Energinets åbne data ([Energi Data Service](https://www.energidataservice.dk/)):

- **Dagsgraf** for en valgt dag med farver for billigste/dyreste tredjedel og markering af
  «nu», samt nøgletal: pris lige nu, billigst, dyrest, gennemsnit og de **billigste 3 timer i træk**.
  Man kan skifte mellem DK1 (Vestdanmark) og DK2 (Østdanmark), bladre mellem dage (også i morgen,
  når priserne er offentliggjort, normalt omkring kl. 13), vælge timegennemsnit eller den
  opløsning, Energinet leverer, og slå moms til. Grafen kan også ses som tabel.
- **Udvikling**: gennemsnitspris pr. dag og **typisk døgnprofil** (hvilke timer er normalt billigst)
  over 7, 30 eller 90 dage.
- Priserne er rene **spotpriser uden** nettarif, elafgift, abonnement og leverandørtillæg; «inkl. moms»
  lægger kun 25 % oven på spotprisen. Enhed er øre/kWh (1 DKK/MWh = 0,1 øre/kWh).
- Alle datoer og dage er danske kalenderdage (korrekt også ved sommer-/vintertid, hvor en dag er
  23 eller 25 timer). Tidsopløsningen aflæses af data, så koden virker både med timer og kvarterer.

### Hentning af priser

Priserne hentes af en kommando, som kan køres så ofte, man vil (gemte priser opdateres blot):

```bash
python manage.py fetch_elpriser                  # i går, i dag og i morgen, begge områder
python manage.py fetch_elpriser --days-back 90   # hent historik (hentes i bidder á 30 dage)
python manage.py fetch_elpriser --area DK1       # kun ét område
python manage.py fetch_elpriser --check-api      # kontrollér forbindelse og datasættets opbygning
```

Planlæg den med cron, fx hver time (og ekstra tæt omkring offentliggørelsen af næste døgns priser):

```cron
7 * * * *       cd /sti/til/django_projekt && .venv/bin/python manage.py fetch_elpriser
*/10 13-14 * * * cd /sti/til/django_projekt && .venv/bin/python manage.py fetch_elpriser
```

Kommandoen afslutter med en fejlkode, hvis hentningen fejler (så cron/overvågning opdager det), og
hvert forsøg logges (`FetchRun`, ses i admin). Siden viser «opdateret for … siden» og en advarsel,
hvis seneste vellykkede hentning er mere end 26 timer gammel. Super admins har en knap **Opdatér nu**.
Mislykkede kald prøves op til 3 gange. Indstillinger (miljøvariabler): `ENERGINET_API_URL`
(standard `https://api.energidataservice.dk`), `ENERGINET_DATASET` (standard `DayAheadPrices`),
`ENERGINET_TIMEOUT` (sekunder, standard 30).

> **Kør `python manage.py fetch_elpriser --check-api` første gang.** Koden blev skrevet i et miljø
> uden adgang til Energinets API og dokumentation, så datasættets navn og kolonner (`DayAheadPrices`
> med `TimeUTC`, `PriceArea`, `DayAheadPriceDKK`) er bygget efter, hvad der var kendt, og testene
> bruger en erstattet netværksforbindelse. Kommandoen kalder det rigtige API, viser kolonner og
> tidsopløsning og fejler tydeligt, hvis noget afviger. Priserne bruges kun, hvis de kan tolkes.

## Layout og menu

Sitet bruger Bootstrap 5.3 og Bootstrap Icons, lagt lokalt i `static/vendor/`
(ingen CDN nødvendig).

- `templates/base.html` er fælles layout. Nye sider arver med `{% extends "base.html" %}`
  og fylder blokken `content` (evt. `title`, `extra_head`, `extra_js`).
- `templates/includes/navbar.html` er menuen. Tilføj et punkt ved at kopiere et
  `<li class="nav-item">` og en `{% url ... as ... %}`-linje øverst.
- `static/css/site.css` og `static/js/theme.js` er egne styles og lyst/mørkt tema.
- `pages`-appen har forside og "Om"-side som eksempler.

## Brugere og roller

Alt med brugere ligger i `accounts`-appen.

- **Tilmelding** (`/tilmeld/`): alle kan oprette en konto og bliver logget ind med det samme.
  Nye brugere er altid almindelige brugere.
- **Login/logout** (`/login/`, `/logout/`): via menuen. Efter login går man tilbage til siden,
  man kom fra (`?next=`). Logout sendes som POST.
- **Roller** bygger på Djangos egne felter:

  | Rolle       | Felter                            | Kan                                          |
  |-------------|-----------------------------------|----------------------------------------------|
  | Bruger      | `is_superuser=False`              | Bruge sitet                                  |
  | Super admin | `is_superuser=True`, `is_staff=True` | Alt ovenstående + `/brugere/` + `/admin/` |

- **Brugeradministration** (`/brugere/`, kun super admin): se alle brugere, skifte rolle og
  slette brugere (med bekræftelsesside). Almindelige brugere får en 403-side.
  Man kan ikke slette sig selv, og den sidste super admin kan hverken slettes eller
  gøres til almindelig bruger.
- Første super admin oprettes med `python manage.py createsuperuser`.

Vil du kræve en rolle på en side, så brug `SuperAdminRequiredMixin` fra
`accounts/permissions.py` (eller `LoginRequiredMixin` for blot at kræve login).
Flere roller kan tilføjes med Djangos grupper (`Group`) og rettigheder.

## Admin

Admin ligger på `/admin/` og kører på en egen `AdminSite` (`backoffice/sites.py`),
der erstatter `django.contrib.admin` i `INSTALLED_APPS`. Derfor registrerer
nye apps sig som normalt med `@admin.register(Model)`.

Der bygges videre på den ved at:
- tilføje nøgletal/widgets til dashboardet i `get_dashboard_context()` og vise dem
  i `templates/backoffice/index.html`
- ændre titler og branding på `BackofficeAdminSite`
- oprette en `ModelAdmin` i hver apps `admin.py` (se `notes/admin.py`)

## Tests

```bash
python manage.py test
```
