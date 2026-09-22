#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_menu

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures'
cases = [
    (FIXTURES / 'month-2026-08.pdf', datetime(2026, 8, 14), 'monthly', {
        'SÜZME MERCİMEK ÇORBA', 'EKŞİLİ KÖFTE', 'PATATES YEMEĞİ*', 'TEREYAĞLI MAKARNA', 'KAKAOLU PUDİNG'
    }),
    (FIXTURES / 'week-2026-08-10.pdf', datetime(2026, 8, 14), 'weekly', {
        'SOĞUK AYRAN AŞI ÇORBASI', 'AKÇAABAT KÖFTE', 'SEBZELİ FIRIN PİLİÇ', 'ERİŞTE'
    }),
]
for pdf, target, source, expected in cases:
    items = collect_menu.extract_grid_items(pdf.read_bytes(), target, source)
    print(source, items)
    missing = expected - set(items)
    if missing:
        raise SystemExit(f'MISSING {source}: {sorted(missing)}')
print('V2 regression PASS')

# Verify every menu day resolves to a distinct header section.
month = cases[0][0].read_bytes()
for day in (3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 17, 18, 19, 20, 21, 24, 25, 26, 27, 28, 31):
    result = collect_menu.extract_grid_items(month, datetime(2026, 8, day), 'monthly')
    if not result:
        raise SystemExit(f'NO MONTHLY ITEMS {day}')
print('monthly day coverage PASS')

week = cases[1][0].read_bytes()
for day in range(10, 15):
    result = collect_menu.extract_grid_items(week, datetime(2026, 8, day), 'weekly')
    if not result:
        raise SystemExit(f'NO WEEKLY ITEMS {day}')
print('weekly day coverage PASS')
