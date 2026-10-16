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
