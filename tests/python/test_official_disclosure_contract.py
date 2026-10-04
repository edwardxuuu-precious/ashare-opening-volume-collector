import _bootstrap
from copy import deepcopy
from itertools import product
import json
import unittest
from unittest.mock import patch

from scripts.backfill_quotes import validate_cache
from scripts.exchange_status import valid_historical_no_trade


DAY = '2026-04-29'
CODE = '002175'
STORED_URL = ('https://disc.static.szse.cn/disc/disk03/finalpage/'
              '2026-04-29/01cd3652-6986-4e3d-beb3-7c702124695b.PDF')
FROZEN_URL = STORED_URL.replace('/disc/disk03/', '/download/disc/disk03/')
URL_FIELDS = ('sourceUrl', 'resumeUrl', 'specialStatus.announcementUrl')


def stored_evidence():
    """Replay the exact frozen record plus its three audited stored URL values.

    The production comparison on 2026-10-04 found no other field difference.
    Both URLs returned the same official PDF (SHA256 99fa56aab391cff5d1fa210fc
    06b82f455e3d311b2d16ed212eedd111fae4c9f), without changing market data.
    """
    return dict(
        kind='official_disclosure_suspension', provider='szse', code=CODE,
        date=DAY, startDate=DAY, endDate=DAY, resumeDate='2026-04-30',
        reason='实施退市风险警示', sourceUrl=STORED_URL, resumeUrl=STORED_URL,
        validatedDates=[DAY], specialStatus=dict(
            type='suspension', label='退市风险警示停牌',
            description='深交所公告显示该股自2026-04-29起因「实施退市风险警示」停牌，'
                        '并于2026-04-30开市起复牌；目标交易日无交易。',
            startedAt=DAY, source='深圳证券交易所公司公告',
            announcementTitle='关于公司股票交易被实施退市风险警示暨股票停牌的公告',
            announcementUrl=STORED_URL))


def set_field(record, field, value):
    if field.startswith('specialStatus.'):
        record['specialStatus'][field.split('.', 1)[1]] = value
    else:
        record[field] = value


class OfficialDisclosureContractTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.create_connection', 'socket.socket.connect'):
            blocked = patch(target, side_effect=AssertionError('Network forbidden in contract tests'))
            blocked.start()
            self.addCleanup(blocked.stop)

    def validate_through_cache(self, evidence, code=CODE, day=DAY):
        cache = dict(version=2, quotes={}, statusEvidence={day: {code: evidence}})
        return validate_cache(cache)

    def assert_rejected(self, evidence, code=CODE, day=DAY):
        self.assertFalse(valid_historical_no_trade(evidence, code, day))
        with self.assertRaisesRegex(ValueError, 'Invalid price backfill status evidence'):
            self.validate_through_cache(evidence, code, day)

    def test_audited_stored_alias_passes_real_cache_validation_without_rewriting(self):
        evidence = stored_evidence()
        before = json.dumps(evidence, sort_keys=True, ensure_ascii=False)

        result = self.validate_through_cache(evidence)

        self.assertIs(result['statusEvidence'][DAY][CODE], evidence)
        self.assertTrue(valid_historical_no_trade(evidence, CODE, DAY))
        self.assertEqual(json.dumps(evidence, sort_keys=True, ensure_ascii=False), before)

    def test_exact_frozen_urls_still_pass(self):
        evidence = stored_evidence()
        for field in URL_FIELDS:
            set_field(evidence, field, FROZEN_URL)
        self.validate_through_cache(evidence)
        self.assertTrue(valid_historical_no_trade(evidence, CODE, DAY))

    def test_each_citation_accepts_only_the_two_verified_urls(self):
        for urls in product((STORED_URL, FROZEN_URL), repeat=3):
            with self.subTest(urls=urls):
                evidence = stored_evidence()
                for field, url in zip(URL_FIELDS, urls):
                    set_field(evidence, field, url)
                self.validate_through_cache(evidence)

    def test_unverified_urls_fail_at_every_citation(self):
        unverified = (
            STORED_URL.replace('disc.static.szse.cn', 'example.com'),
            STORED_URL.replace('disc.static.szse.cn', 'disc.static.szse.cn.example.com'),
            STORED_URL.replace('https://', 'http://'),
            STORED_URL.replace('https://', 'https://user@'),
            STORED_URL.replace('.cn/disc', '.cn:443/disc'),
            STORED_URL.replace('2026-04-29/', '2026-04-30/'),
            STORED_URL.replace('01cd3652-', '01cd3653-'),
            STORED_URL.replace('.PDF', '.pdf'),
            STORED_URL.replace('finalpage/', 'finalpage/%32'),
            STORED_URL + '?download=1',
            FROZEN_URL + '?download=1',
            STORED_URL + '#page=1',
            STORED_URL + '/',
            FROZEN_URL.replace('/download/', '/download/download/'),
            None, [], {}, 1,
        )
        for field, url in product(URL_FIELDS, unverified):
            with self.subTest(field=field, url=url):
                evidence = stored_evidence()
                set_field(evidence, field, url)
                self.assert_rejected(evidence)

    def test_identity_date_and_disclosure_content_remain_exact(self):
        changes = (
            ('kind', 'market_suspension_record'),
            ('provider', 'sse'), ('code', '000016'), ('date', '2026-04-30'),
            ('validatedDates', ['2026-04-29', '2026-04-30']),
            ('startDate', '2026-04-28'), ('endDate', '2026-04-30'),
            ('resumeDate', '2026-05-06'), ('reason', '其他原因'),
            ('specialStatus.type', 'major_restructuring'),
            ('specialStatus.label', '其他停牌'), ('specialStatus.description', '其他描述'),
            ('specialStatus.startedAt', '2026-04-28'),
            ('specialStatus.source', '其他来源'),
            ('specialStatus.announcementTitle', '其他公告'),
            ('extraField', True), ('specialStatus', None),
        )
        for field, value in changes:
            with self.subTest(field=field):
                evidence = stored_evidence()
                set_field(evidence, field, value)
                self.assert_rejected(evidence)
        self.assert_rejected(stored_evidence(), code='000016')
        self.assert_rejected(stored_evidence(), day='2026-04-30')

    def test_download_alias_of_another_official_document_is_not_registered(self):
        evidence = dict(
            kind='official_disclosure_suspension', provider='szse', code='000016',
            date=DAY, startDate=DAY, endDate=DAY, resumeDate='2026-04-30',
            reason='实施退市风险警示及其他风险警示',
            sourceUrl='https://disc.static.szse.cn/disc/disk03/finalpage/'
                      '2026-04-29/33994a43-0f3a-4776-8857-ed8163a15ce6.PDF',
            resumeUrl='https://disc.static.szse.cn/disc/disk03/finalpage/'
                      '2026-04-29/33994a43-0f3a-4776-8857-ed8163a15ce6.PDF',
            validatedDates=[DAY], specialStatus=dict(
                type='suspension', label='退市及其他风险警示停牌',
                description='深交所公告显示该股自2026-04-29起因「实施退市风险警示及其他风险警示」停牌，'
                            '并于2026-04-30开市起复牌；目标交易日无交易。',
                startedAt=DAY, source='深圳证券交易所公司公告',
                announcementTitle='关于公司股票被实施退市风险警示及其他风险警示暨股票停复牌的提示性公告',
                announcementUrl='https://disc.static.szse.cn/disc/disk03/finalpage/'
                                '2026-04-29/33994a43-0f3a-4776-8857-ed8163a15ce6.PDF'))
        self.validate_through_cache(evidence, code='000016')
        for field in URL_FIELDS:
            changed = deepcopy(evidence)
            original = evidence['specialStatus']['announcementUrl'] if field.startswith('specialStatus.') else evidence[field]
            set_field(changed, field, original.replace('/disc/disk03/', '/download/disc/disk03/'))
            self.assert_rejected(changed, code='000016')


if __name__ == '__main__':
    unittest.main()
