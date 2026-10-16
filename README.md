# Django noter

Et simpelt Django 6.1-projekt med en note-app (opret, vis, slet + admin).

## Kom i gang

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser   # valgfrit, til /admin/
python manage.py runserver
```

Åbn http://127.0.0.1:8000/ — admin ligger på http://127.0.0.1:8000/admin/.

## Tests

```bash
python manage.py test
```
