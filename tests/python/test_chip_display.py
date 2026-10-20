"""Invented distribution transport checks; no source fetch or strategy trial."""
import copy
import unittest
import time
from decimal import Decimal
from unittest.mock import patch
from backend.chips.display import display_frame
from backend.chips.runner import _calculate,implementation_identity
from test_chip_daily_v1 import Memory,FakeSource,STOCK,DAYS


class DisplayTransport(unittest.TestCase):
    def test_grouping_preserves_mass_exact_anchors_and_original_calculation(self):
        def snapshot(peak):
            points=[{'price_cny':f'{Decimal(i)/100:.2f}','share_percent':'0.02'} for i in range(1,5001)]
            points[0]['share_percent']='0.01';points[int(Decimal(peak)*100)-1]['share_percent']='0.03'
            return {'profile':points,'peak_prices_cny':[peak],'peak_bin_share_percent':'0.03','model_mean_cost_cny':'25.01499998'}
        original={'security_id':'ALL_VALUES_INVENTED','unchanged_threshold':None,
            'snapshots':{'2026-10-15':snapshot('20.00'),'2026-10-16':snapshot('25.00')}}
        before=copy.deepcopy(original)
        shown=display_frame(original)
        self.assertEqual(original,before)
        for day,snapshot in shown['snapshots'].items():
            self.assertLessEqual(len(snapshot['profile']),4096)
            self.assertEqual(sum(Decimal(p['share_percent']) for p in snapshot['profile']),100)
            self.assertEqual(snapshot['peak_prices_cny'],original['snapshots'][day]['peak_prices_cny'])
            self.assertEqual(snapshot['model_mean_cost_cny'],original['snapshots'][day]['model_mean_cost_cny'])
            exact={p['price_cny']:p['share_percent'] for p in original['snapshots'][day]['profile']}
            for price in ('20.00','25.00'):
                self.assertEqual(snapshot['exact_anchor_shares_percent'][price],exact[price])
                legacy=next((p['share_percent'] for p in snapshot['profile'] if p['price_cny']==price),'0')
                self.assertEqual(legacy,exact[price])
            self.assertEqual(snapshot['original_profile_bin_count'],5000)
        self.assertIs(display_frame(shown),shown)
        self.assertIsNone(shown['unchanged_threshold'])

    def test_legacy_lookup_does_not_read_group_mass_at_a_vanished_anchor(self):
        previous=[{'price_cny':f'{Decimal(i)/100:.2f}','share_percent':'0.02'} for i in range(1,5001)]
        current=[{'price_cny':f'{Decimal(i)/100:.2f}','share_percent':'0.02'} for i in range(1,10000,2)]
        value={'snapshots':{'2026-10-15':{'profile':previous,'peak_prices_cny':['20.00']},
            '2026-10-16':{'profile':current,'peak_prices_cny':['25.01']}}}
        shown=display_frame(value)['snapshots']['2026-10-16']
        self.assertEqual(shown['exact_anchor_shares_percent']['20.00'],'0')
        self.assertEqual(next((p['share_percent'] for p in shown['profile'] if p['price_cny']=='20.00'),'0'),'0')
        self.assertEqual(sum(Decimal(p['share_percent']) for p in shown['profile']),100)
        self.assertLessEqual(len(shown['profile']),4096)

    def test_full_precision_archive_precedes_display_and_lost_lease_prevents_publication(self):
        for revoke in (False,True):
            with self.subTest(revoke=revoke):
                store=Memory();owner='INVENTED';dataset='1111111111111111'
                store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+1000})
                put=store.put
                def save(key,value,**kwargs):
                    result=put(key,value,**kwargs)
                    if revoke and key.startswith('collector/chips/full-frames/'):
                        put('collector/chips/lease.json',{'owner':'OTHER_INVENTED_WRITER','expires':time.time()+1000})
                    return result
                store.put=save
                points=[{'price_cny':f'{Decimal(i)/100:.2f}','share_percent':'0.02'} for i in range(1,5001)]
                value={'dataset_id':dataset,'security_id':STOCK['code'],'source':{'sha256':'INVENTED'},
                    'snapshots':{DAYS[-1]:{'profile':points,'peak_prices_cny':['25.00']}}}
                row={'security_id':STOCK['code'],'chip_status':'READY','reasons':[]}
                context={'slot':dataset,'dataset':dataset,'days':DAYS,'as_of':DAYS[-1],'owner':owner,
                    'pools':{},'execution_identity':implementation_identity()}
                with patch('backend.chips.runner.frame',return_value=(row,value)):
                    if revoke:
                        with self.assertRaisesRegex(RuntimeError,'LEASE_LOST'):_calculate(store,FakeSource(),STOCK,context)
                    else:_calculate(store,FakeSource(),STOCK,context)
                full_key=f'collector/chips/full-frames/{dataset}/{STOCK["code"]}.json'
                public_key=f'data/chips/daily/{dataset}/{STOCK["code"]}.json'
                self.assertEqual(len(store.get(full_key)[0]['snapshots'][DAYS[-1]]['profile']),5000)
                if revoke:self.assertIsNone(store.get(public_key)[0])
                else:
                    shown=store.get(public_key)[0]
                    self.assertEqual(shown['full_precision_frame_private_key'],full_key)
                    self.assertLessEqual(len(shown['snapshots'][DAYS[-1]]['profile']),4096)
                    self.assertLess(store.writes.index(full_key),store.writes.index(public_key))

    def test_unrenderable_exact_fallback_is_archived_but_never_published(self):
        store=Memory();owner='INVENTED';dataset='2222222222222222'
        store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+1000})
        points=[{'price_cny':str(i),'share_percent':'25'} for i in range(1,5)]
        value={'dataset_id':dataset,'security_id':STOCK['code'],'source':{'sha256':'INVENTED'},
            'snapshots':{DAYS[-1]:{'profile':points,'peak_prices_cny':['1','2','3','4']}}}
        row={'security_id':STOCK['code'],'chip_status':'READY','reasons':[]}
        context={'slot':dataset,'dataset':dataset,'days':DAYS,'as_of':DAYS[-1],'owner':owner,
            'pools':{},'execution_identity':implementation_identity()}
        with patch('backend.chips.runner.frame',return_value=(row,value)),patch('backend.chips.runner.MAX_BROWSER_PROFILE_BINS',3):
            with self.assertRaisesRegex(ValueError,'EXCEEDS_BROWSER_PROFILE_LIMIT'):
                _calculate(store,FakeSource(),STOCK,context)
        full=store.get(f'collector/chips/full-frames/{dataset}/{STOCK["code"]}.json')[0]
        self.assertEqual(full['snapshots'][DAYS[-1]]['profile'],points)
        self.assertEqual(len(full['archived_display_profile_sha256'][DAYS[-1]]),64)
        self.assertIsNone(store.get(f'data/chips/daily/{dataset}/{STOCK["code"]}.json')[0])


if __name__=='__main__':unittest.main()
