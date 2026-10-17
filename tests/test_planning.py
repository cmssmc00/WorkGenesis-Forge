"""Determinism, scale, exclusions, and actual O*NET row provenance."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from wgforge.planning import PlanningError, plan_tasks


ROOT = Path(__file__).resolve().parents[1]


def inventory(count=20):
    return [{'profession': f'Test occupation {i}', 'occupation_code': f'99-{i:04d}.00',
             'activities': [{'activity': 'Review evidence', 'activity_id': 'act-1'}, {'activity': 'Analyze records', 'activity_id': 'act-2'}],
             'deliverable_families': ['spreadsheet', 'document']} for i in range(count)]


class PlanningTests(unittest.TestCase):
    def test_seed_reproducibility_and_unique_ids(self):
        config = {'inventory': inventory(), 'seed': 42, 'count': 20}
        a, b = plan_tasks(config), plan_tasks(config)
        self.assertEqual(a, b)
        self.assertEqual(len({r['plan_id'] for r in a}), 20)
        self.assertNotEqual(a, plan_tasks({**config, 'seed': 43}))
    def test_inventory_order_does_not_change_plan(self):
        data = inventory()
        config = {'inventory': data, 'seed': 2, 'count': 10}
        a = plan_tasks(config)
        self.assertEqual(a, plan_tasks({**config, 'inventory': list(reversed(data))}))
    def test_dynamic_new_professions_are_not_hardcoded(self):
        rows = plan_tasks({'inventory': inventory(120), 'count': 480, 'seed': 17})
        self.assertEqual(len(rows), 480)
        self.assertEqual(len({r['profession'] for r in rows}), 120)
        self.assertTrue(all(r['inventory_origin'] == 'user_inventory' for r in rows))
    def test_exclusions_apply_before_sampling(self):
        rows = plan_tasks({'inventory': inventory(5), 'count': 4, 'exclusions': {'occupation_codes': ['99-0000.00'], 'deliverable_families': ['document'], 'activity_ids': ['act-2']}})
        self.assertTrue(all(r['occupation_code'] != '99-0000.00' and r['deliverable_family'] == 'spreadsheet' and r['activity_id'] == 'act-1' for r in rows))
    def test_explicit_heldout_never_in_training(self):
        config = {'inventory': inventory(5), 'heldout': {'occupation_codes': ['99-0000.00']}, 'seed': 12}
        train = plan_tasks({**config, 'count': 16})
        test = plan_tasks({**config, 'split': 'heldout', 'count': 4})
        self.assertTrue(all(r['occupation_code'] != '99-0000.00' for r in train))
        self.assertTrue(all(r['occupation_code'] == '99-0000.00' for r in test))
    def test_fraction_heldout_groups_all_families(self):
        config = {'inventory': inventory(100), 'heldout_fraction': .5, 'count': 150, 'seed': 5}
        train, test = plan_tasks(config), plan_tasks({**config, 'split': 'heldout'})
        groups = lambda rows: {(r['occupation_code'], r['activity_id']) for r in rows}
        self.assertFalse(groups(train) & groups(test))
    def test_empty_or_insufficient_inventory_fails(self):
        for config in [{'inventory': [], 'count': 1}, {'inventory': inventory(1), 'count': 5}, {'inventory': inventory(1), 'count': 0}]:
            with self.subTest(config=config), self.assertRaises(PlanningError):
                plan_tasks(config)
    def test_duplicate_inventory_cannot_inflate_supply(self):
        row = {'profession': 'Duplicate', 'occupation_code': '99-9999.00', 'activity': 'Analyze', 'deliverable_families': ['spreadsheet']}
        with self.assertRaises(PlanningError):
            plan_tasks({'inventory': [row, row], 'count': 2})
    def test_unknown_selector_fails_closed(self):
        with self.assertRaises(PlanningError):
            plan_tasks({'inventory': inventory(), 'exclusions': {'misspelled_codes': ['99-0000.00']}})
    def test_custom_claimed_onet_origin_not_treated_as_verified(self):
        row = {'profession': 'Example', 'occupation_code': '99-9999.00', 'activity': 'Analyze', 'inventory_origin': 'onet_downloadable_database'}
        r = plan_tasks({'inventory': [row], 'count': 1})[0]
        self.assertEqual(r['inventory_origin'], 'user_inventory')
        self.assertFalse(r['inventory_verified'])
    def test_bundled_smoke_has_verified_actual_onet_rows(self):
        rows = plan_tasks({'count': 60, 'seed': 1})
        self.assertEqual(len({r['occupation_code'] for r in rows}), 5)
        self.assertTrue(all(r['activity_provenance']['sha256'] for r in rows))
        self.assertTrue(all(r['inventory_origin'] == 'curated_onet_smoke_subset' for r in rows))
        self.assertIn('19-2042.00', {r['occupation_code'] for r in rows})
    def test_full_official_adapter_scales_beyond_smoke(self):
        config = {'adapter': 'onet', 'onet_manifest_path': str(ROOT / 'examples/anchors/onet_manifest.json'),
                  'count': 1000, 'seed': 77, 'minimum_importance': 3.5}
        rows = plan_tasks(config)
        self.assertEqual(len(rows), 1000)
        self.assertGreater(len({r['occupation_code'] for r in rows}), 100)
        self.assertTrue(all(r['onet_importance'] >= 3.5 for r in rows))
        self.assertTrue(all(r['inventory_origin'] == 'onet_downloadable_database' for r in rows))
    def test_profession_filter(self):
        rows = plan_tasks({'count': 3, 'occupation_codes': ['19-2042.00'], 'deliverable_families': ['spreadsheet']})
        self.assertTrue(all(r['occupation_code'] == '19-2042.00' and r['deliverable_family'] == 'spreadsheet' for r in rows))
    def test_network_adapter_is_explicit_opt_in(self):
        with self.assertRaisesRegex(PlanningError, 'allow_network'):
            plan_tasks({'adapter': 'onet'})
    def test_custom_json_inventory_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'inventory.json'; path.write_text(json.dumps({'inventory': inventory(3)}))
            self.assertEqual(len(plan_tasks({'inventory_path': str(path), 'count': 12})), 12)


if __name__ == '__main__':
    unittest.main()
