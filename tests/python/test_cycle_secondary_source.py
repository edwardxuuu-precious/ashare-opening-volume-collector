import _bootstrap
from datetime import date, timedelta
import json
import unittest

from backend.cycle_bottom.sources import (
    SECONDARY_CODE, SECONDARY_NAME, SECONDARY_ORIGIN,
    fetch_secondary, parse_secondary, secondary_url,
)


def invented_source():
    start = date(2007, 12, 28)
    values = [{'tradeDate': (start + timedelta(days=i)).isoformat(),
               'indexValue': 1000 + i / 10} for i in range(700)]
    info = {'indexCode': SECONDARY_CODE, 'indexName': SECONDARY_NAME,
            'baseDate': '2007-12-28', 'baseValue': 1000, 'publishDate': '2022-04-01',
            'tradeDate': values[-1]['tradeDate'], 'indexValue': values[-1]['indexValue'],
            'valueJson': '/json/900330.CSJ_perf.json?t=124'}
    return {'infos': [info]}, {'date': values[-1]['tradeDate'], 'values': values}


class SecondarySource(unittest.TestCase):
    def parse(self, catalog, history, *, calendar=None, end=None):
        calendar = calendar if calendar is not None else {row['tradeDate'] for row in history['values']}
        return parse_secondary(json.dumps(catalog), json.dumps(history),
                               end=end or history['date'], calendar=calendar)

    def test_category_identity_must_match_the_catalog_not_just_a_bond_label(self):
        catalog, history = invented_source()
        rows, _, info = self.parse(catalog, history)
        self.assertEqual(len(rows), 700)
        self.assertEqual(info['indexCode'], '900330.CSJ')
        catalog['infos'][0]['indexName'] = '万得混合债券型二级基金指数'
        with self.assertRaisesRegex(ValueError, 'SOURCE_IDENTITY'):
            self.parse(catalog, history)

    def test_catalog_and_series_disagreement_never_becomes_a_current_quote(self):
        catalog, history = invented_source()
        catalog['infos'][0]['indexValue'] += 1
        with self.assertRaisesRegex(ValueError, 'CATALOG_HISTORY_MISMATCH'):
            self.parse(catalog, history)

    def test_duplicate_dates_are_rejected_instead_of_counted_twice(self):
        catalog, history = invented_source()
        history['values'][40] = history['values'][39].copy()
        with self.assertRaisesRegex(ValueError, 'DATE_ORDER'):
            self.parse(catalog, history)

    def test_future_and_non_session_values_do_not_enter_the_comparison(self):
        catalog, history = invented_source()
        calendar = {row['tradeDate'] for row in history['values']}
        calendar.remove(history['values'][40]['tradeDate'])
        end = history['values'][-6]['tradeDate']
        rows, excluded, _ = self.parse(catalog, history, calendar=calendar, end=end)
        self.assertEqual(excluded, 6)
        self.assertEqual(rows[-1]['date'], end)
        self.assertNotIn(history['values'][40]['tradeDate'], {row['date'] for row in rows})

    def test_published_site_links_cannot_redirect_the_fetcher_to_other_sources(self):
        for value in ['https://other.invalid/json/fund_index.json', '//other.invalid/json/fund_index.json',
                      '/json/900320.CSJ_perf.json', '/json/fund_index.json?other=1']:
            with self.assertRaisesRegex(ValueError, 'SOURCE_PATH'):
                secondary_url(value, '/json/fund_index.json')

    def test_fetch_keeps_all_three_provenance_inputs_and_uses_published_links(self):
        catalog, history = invented_source()
        site = {'fundIndexJson': '/json/fund_index.json?t=123'}
        responses = {SECONDARY_ORIGIN + '/json/site.json': json.dumps(site).encode(),
                     SECONDARY_ORIGIN + site['fundIndexJson']: json.dumps(catalog).encode(),
                     SECONDARY_ORIGIN + catalog['infos'][0]['valueJson']: json.dumps(history).encode()}
        calls = []

        def fetcher(url):
            calls.append(url)
            return responses[url]

        rows, source, raw = fetch_secondary(fetcher=fetcher, end=history['date'],
                                             calendar={row['tradeDate'] for row in history['values']})
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(rows), 700)
        self.assertEqual(source['missingSessions'], 0)
        self.assertEqual(source['publishDate'], '2022-04-01')
        self.assertEqual(set(raw), {'secondary', 'secondary-site', 'secondary-catalog'})


if __name__ == '__main__':
    unittest.main()
