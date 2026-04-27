import polib, json, sys
sys.stdout.reconfigure(encoding='utf-8')


def is_out_of_scope(occ_path):
    p = occ_path.replace('\\', '/').lower().lstrip('./')
    if '/admin/' in p and p.endswith('.py'):
        return True
    if '/templates/admin/' in p or p.startswith('templates/admin/'):
        return True
    return False


def bucket_of(refs):
    has_user_tpl = has_coach_tpl = has_model = has_form = has_schema = has_view = False
    for r in refs:
        p = r.replace('\\', '/').lower()
        if 'templates/' in p:
            if 'admin' in p or 'coach' in p or 'review' in p:
                has_coach_tpl = True
            else:
                has_user_tpl = True
        elif 'models/' in p:
            has_model = True
        elif 'forms.py' in p or 'forms_' in p:
            has_form = True
        elif 'pre_screening_schema' in p:
            has_schema = True
        elif 'views' in p:
            has_view = True
    if has_user_tpl:
        return 'user_tpl'
    if has_coach_tpl:
        return 'coach_tpl'
    if has_form or has_model or has_schema:
        return 'models_forms'
    if has_view:
        return 'views'
    return 'other'


de = polib.pofile('crush_lu/locale/de/LC_MESSAGES/django.po')
fr = polib.pofile('crush_lu/locale/fr/LC_MESSAGES/django.po')

de_fz = {e.msgid: e for e in de.fuzzy_entries()
         if e.occurrences and not all(is_out_of_scope(o[0]) for o in e.occurrences)}
fr_fz = {e.msgid: e for e in fr.fuzzy_entries()
         if e.occurrences and not all(is_out_of_scope(o[0]) for o in e.occurrences)}
all_ids = sorted(set(de_fz.keys()) | set(fr_fz.keys()))

buckets = {'user_tpl': [], 'coach_tpl': [], 'models_forms': [], 'views': [], 'other': []}
for mid in all_ids:
    e = de_fz.get(mid) or fr_fz.get(mid)
    refs = sorted(set(o[0].replace('\\', '/').lstrip('./') for o in e.occurrences))
    rec = {
        'msgid': mid,
        'msgid_plural': e.msgid_plural or None,
        'refs': refs,
        'de': mid in de_fz,
        'fr': mid in fr_fz,
    }
    buckets[bucket_of(refs)].append(rec)

for name, items in buckets.items():
    with open(f'_i18n_fz_{name}.json', 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f'{name}: {len(items)}')
