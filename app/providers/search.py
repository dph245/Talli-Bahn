"""Shared station-name normalization for timetable and demo searches."""
import unicodedata


def normalize_name(value):
    value = unicodedata.normalize('NFC', value).casefold()
    for source, target in [('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('hauptbahnhof', 'hbf')]:
        value = value.replace(source, target)
    return value


def search_terms(query):
    return list(dict.fromkeys(normalize_name(query).split()))


def like_pattern(value):
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


# search_name is already casefolded by the importer. No database rebuild needed.
SEARCH_NAME_SQL = "replace(replace(replace(replace(search_name, 'ä', 'ae'), 'ö', 'oe'), 'ü', 'ue'), 'hauptbahnhof', 'hbf')"
